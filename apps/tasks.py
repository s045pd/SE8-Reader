import asyncio
import base64
from contextlib import contextmanager
from logging import getLogger

from asgiref.sync import sync_to_async
from celery import shared_task
from celery_once import QueueOnce
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.db.models import Exists, OuterRef, Q

from apps.models import Book, Episode, Image, Tag
from apps.services import ImageExtractor
from apps.tools import images_to_long_image, long_image_to_pdf
from SE8 import celery_app

logger = getLogger("celery")


@contextmanager
def async_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()


async def process_books():
    async for data in ImageExtractor().get_books():
        current_episode = data.pop("current", None)
        book, created = await sync_to_async(Book.objects.update_or_create)(
            id=data.pop("id"),
            defaults=data,
        )
        if created or await sync_to_async(book.is_outdated)(
            episodes_title=current_episode
        ):
            logger.info(f"Find book: {book.title}")
            find_episodes.apply_async(args=[book.id], countdown=5)


@celery_app.task(base=QueueOnce, once={"graceful": True})
def find_books():
    """
    Find books from the website and create or update Book objects
    Usage: from apps.tasks import find_books as t;t();
    """
    with async_event_loop() as loop:
        loop.run_until_complete(process_books())


async def process_episodes(book_id: str):
    try:
        book = await sync_to_async(Book.objects.get)(id=book_id)
    except ObjectDoesNotExist:
        logger.error(f"Book with id {book_id} does not exist.")
        return

    async for data in ImageExtractor().get_episodes(book.raw_url):
        if "tags" in data:
            for tag in data["tags"]:
                await sync_to_async(
                    lambda: book.tags.add(Tag.objects.get_or_create(name=tag)[0])
                )()
            book.hot = data["hot"]
            book.description = data["description"]
            await sync_to_async(book.save)(update_fields=["hot", "description"])
        else:
            episode, created = await sync_to_async(Episode.objects.update_or_create)(
                id=data.pop("id"),
                book=book,
                defaults=data,
            )
            if created:
                logger.info(f"Find episode: {episode.title}")
                find_images.apply_async(args=[episode.id], countdown=5)


@shared_task
def find_episodes(book_id: str, start_index: int | None = None):
    """
    Find episodes for a specific book and create or update Episode objects
    Usage: from apps.models import Book, Episode;from apps.tasks import find_episodes as t;t( Book.objects.first().id );
    """
    with async_event_loop() as loop:
        loop.run_until_complete(process_episodes(book_id))


async def process_images(episode_id: str):
    episode = await sync_to_async(Episode.objects.get)(pk=episode_id)
    images_task = []
    async for data in ImageExtractor().get_images(episode.raw_url):
        image, created = await sync_to_async(Image.objects.update_or_create)(
            id=data.pop("id"),
            episode=episode,
            defaults=data,
        )
        if created:
            images_task.append([image.id, image.raw_url])
            logger.info(f"Find image: {episode.title} - {image.index}")

    downloaded_images = await ImageExtractor().get_images_concurrently_with_id(
        images_task
    )
    for key, image in downloaded_images:
        image_obj = await sync_to_async(Image.objects.get)(pk=key)
        image_obj.image = base64.b64encode(image).decode()
        await sync_to_async(image_obj.save)(update_fields=["image"])


@shared_task
def find_images(episode_id: str):
    """
    Find images for a specific episode and create or update Image objects
    Usage: from apps.models import Episode;from apps.tasks import find_images as t;t( Episode.objects.first().id );
    """
    with async_event_loop() as loop:
        loop.run_until_complete(process_images(episode_id))


@shared_task
def download_image(image_id: str, force: bool = False):
    """
    Download image for a specific Image object
    Usage: from apps.tasks import download_image as t;t();
    """
    image = asyncio.run(sync_to_async(Image.objects.get)(pk=image_id))
    if not force and image.image:
        return
    with async_event_loop() as loop:
        image_content = loop.run_until_complete(
            ImageExtractor().download_image(image.raw_url)
        )
    image.image = image_content
    asyncio.run(sync_to_async(image.save)(update_fields=["image"]))


@shared_task
def download_images(images_id_list: list):
    """
    Download images for a specific episode
    Usage: from apps.models import Episode;from apps.tasks import download_images as t;t( Episode.objects.first().id );
    """

    images = asyncio.run(
        sync_to_async(
            lambda: list(
                Image.objects.filter(id__in=images_id_list)
                .only("id", "raw_url")
                .values_list("id", "raw_url")
            )
        )()
    )

    with async_event_loop() as loop:
        images_result = loop.run_until_complete(
            ImageExtractor().get_images_concurrently_with_id(images)
        )
        for key, image in images_result:
            image_obj = asyncio.run(sync_to_async(Image.objects.get)(pk=key))
            image_obj.image = base64.b64encode(image).decode()
            asyncio.run(sync_to_async(image_obj.save)(update_fields=["image"]))


@celery_app.task(base=QueueOnce, once={"graceful": True, "timeout": 60 * 60 * 24})
def fix_images():
    """
    Fix missing images for Book and Image objects
    Usage: from apps.tasks import fix_images as t;t();
    """
    for model, image_attr in [(Book, "image_url"), (Image, "raw_url")]:
        for obj in asyncio.run(
            sync_to_async(
                lambda: list(model.objects.filter(image="").only("id", image_attr))
            )()
        ):
            if isinstance(obj, Image):
                download_image.apply_async(args=[obj.id], countdown=5)
            else:
                with async_event_loop() as loop:
                    obj.image = loop.run_until_complete(
                        ImageExtractor().download_image(getattr(obj, image_attr))
                    )
                asyncio.run(sync_to_async(obj.save)(update_fields=["image"]))


async def process_convert_to_pdf(episode_id: str, force: bool = False):
    episode = await sync_to_async(Episode.objects.get)(pk=episode_id)
    images = await sync_to_async(
        lambda: list(
            episode.images.all().order_by("index").values_list("image", flat=True)
        )
    )()
    images = [base64.b64decode(img) for img in images if img]

    if not images:
        return

    combined_image = await images_to_long_image(images, use_process_pool=False)
    pdf_buffer = await long_image_to_pdf(combined_image, use_process_pool=False)

    if pdf_buffer:
        await sync_to_async(
            lambda: episode.pdf.save(
                f"{episode.title}.pdf", ContentFile(pdf_buffer.read())
            )
        )()
        await sync_to_async(episode.save)(update_fields=["pdf"])
        logger.info(f"Convert to PDF: {episode.title}")


@shared_task
def convert_to_pdf(episode_id: str, force: bool = False):
    """
    Convert images of an episode to PDF
    Usage: from apps.models import Episode;from apps.tasks import convert_to_pdf as t;t( Episode.objects.first().id );
    """
    with async_event_loop() as loop:
        loop.run_until_complete(process_convert_to_pdf(episode_id, force))


@celery_app.task(base=QueueOnce, once={"graceful": True, "timeout": 60 * 60 * 24})
def fix_pdf():
    """
    Fix missing PDFs for episodes
    Usage: from apps.tasks import fix_pdf as t;t();
    """
    image_subquery = Image.objects.filter(episode_id=OuterRef("pk"), image="")

    for episode in asyncio.run(
        sync_to_async(
            lambda: list(
                Episode.objects.filter(
                    Q(pdf="") | Q(pdf__isnull=True),
                    images__image__isnull=False,
                )
                .exclude(Exists(image_subquery))
                .distinct()
                .only("id")
            )
        )()
    ):
        convert_to_pdf.apply_async(
            args=[episode.id],
            countdown=5,
            options={"once": {"keys": [episode.id], "timeout": 60 * 60 * 24}},
        )
