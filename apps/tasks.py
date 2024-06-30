from logging import getLogger

from celery import shared_task
from celery_once import QueueOnce
from django.db.models import Exists, OuterRef, Q

from apps.models import Book, Episode, Image, Tag
from apps.services import ImageExtractor
from SE8 import celery_app

logger = getLogger("celery")


@celery_app.task(base=QueueOnce, once={"graceful": True})
def find_books():
    """
    Find books from the website and create or update Book objects
    Usage: from apps.tasks import find_books as t;t();
    """
    for data in ImageExtractor().get_books():
        current_episode = data.pop("current", None)

        book, created = Book.objects.update_or_create(
            id=data.pop("id"),
            defaults=data,
        )

        if created or book.is_outdated(episodes_title=current_episode):
            logger.info(f"Find book: {book.title}")
            find_episodes.apply_async(args=[book.id], countdown=5)


@shared_task
def find_episodes(book_id: str, start_index: int | None = None):
    """
    Find episodes for a specific book and create or update Episode objects
    Usage: from apps.models import Book, Episode;from apps.tasks import find_episodes as t;t( Book.objects.first().id );
    """
    book = Book.objects.get(id=book_id)
    episodes = ImageExtractor().get_episodes(book.raw_url)

    # Update book tags, hot, and description
    try:
        for tag in (book_data := next(episodes)).get("tags", []):
            book.tags.add(Tag.objects.get_or_create(name=tag)[0])

        book.hot = book_data.get("hot", 0)
        book.description = book_data.get("description", "")
        book.save(update_fields=["hot", "description"])
    except StopIteration:
        pass

    # Create or update episodes
    for data in episodes:
        episode, created = Episode.objects.update_or_create(
            id=data.pop("id"),
            book=book,
            defaults=data,
        )

        if created:
            logger.info(f"Find episode: {episode.title}")
            find_images.apply_async(args=[episode.id], countdown=5)


@shared_task
def find_images(episode_id: str):
    """
    Find images for a specific episode and create or update Image objects
    Usage: from apps.models import Episode;from apps.tasks import find_images as t;t( Episode.objects.first().id );
    """
    episode = Episode.objects.get(pk=episode_id)

    for data in ImageExtractor().get_images(episode.raw_url):
        image, created = Image.objects.update_or_create(
            id=data.pop("id"),
            episode=episode,
            defaults=data,
        )
        if created:
            logger.info(f"Find image: {episode.title} - {image.index}")
            download_image.apply_async(args=[image.id], countdown=5)


@shared_task
def download_image(image_id: str, force: bool = False):
    """
    Download image for a specific Image object
    Usage: from apps.tasks import download_image as t;t();
    """
    image = Image.objects.get(pk=image_id)
    if not force and image.image:
        return

    image.image = ImageExtractor().download_image(image.raw_url)
    image.save(update_fields=["image"])


@celery_app.task(base=QueueOnce, once={"graceful": True, "timeout": 60 * 60 * 24})
def fix_images():
    """
    Fix missing images for Book and Image objects
    Usage: from apps.tasks import fix_images as t;t();
    """
    for model, image_attr in [(Book, "image_url"), (Image, "raw_url")]:
        for obj in model.objects.filter(image="").only("id", image_attr).iterator():
            if isinstance(obj, Image):
                download_image.apply_async(args=[obj.id], countdown=5)
            else:
                obj.image = ImageExtractor().download_image(getattr(obj, image_attr))
                obj.save(update_fields=["image"])


@shared_task
def convert_to_pdf(episode_id: str, force: bool = False):
    """
    Convert images of an episode to PDF
    Usage: from apps.models import Episode;from apps.tasks import convert_to_pdf as t;t( Episode.objects.first().id );
    """
    episode = Episode.objects.get(pk=episode_id)
    if episode.convert_to_pdf(force=force):
        logger.info(f"Convert to PDF: {episode.title}")


@celery_app.task(base=QueueOnce, once={"graceful": True, "timeout": 60 * 60 * 24})
def fix_pdf():
    """
    Fix missing PDFs for episodes
    Usage: from apps.tasks import fix_pdf as t;t();
    """
    image_subquery = Image.objects.filter(episode_id=OuterRef("pk"), image="")

    for episode in (
        Episode.objects.filter(
            Q(pdf="") | Q(pdf__isnull=True),
            images__image__isnull=False,
        )
        .exclude(Exists(image_subquery))
        .distinct()
        .only("id")
        .iterator()
    ):
        convert_to_pdf.apply_async(
            args=[episode.id],
            countdown=5,
            options={"once": {"keys": [episode.id], "timeout": 60 * 60 * 24}},
        )
