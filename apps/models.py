# models.py
import base64
import logging

from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.db import models
from PIL import ImageFile

from apps.tools import images_to_long_image, long_image_to_pdf

logger = logging.getLogger(__name__)

ImageFile.LOAD_TRUNCATED_IMAGES = True


class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="tag-name")

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Book(models.Model):
    id = models.CharField(max_length=100, unique=True, db_index=True, primary_key=True)
    image = models.TextField(default="")
    hot = models.IntegerField(default=0)
    title = models.CharField(max_length=100, default="")
    tags = models.ManyToManyField(Tag, related_name="books")
    description = models.TextField(default="")
    raw_url = models.URLField(default="")
    image_url = models.URLField(default="")

    class Meta:
        verbose_name = "Book"
        verbose_name_plural = "Books"
        ordering = ["title"]

    def __str__(self):
        return f"<{self.title} [{self.episodes.count()}]>"

    def is_outdated(self, episodes_title: str) -> bool:
        if not self.episodes.exists():
            return True
        return self.episodes.all().order_by("id").last().title != episodes_title


class Episode(models.Model):
    id = models.IntegerField(primary_key=True)
    title = models.CharField(max_length=100, default="")
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="episodes")
    raw_url = models.URLField(default="")
    pdf = models.FileField(upload_to="pdfs", null=True, blank=True)

    class Meta:
        verbose_name = "Episode"
        verbose_name_plural = "Episodes"
        ordering = ["book__id", "id", "title"]

    def __str__(self):
        return f"{self.book.title} - {self.id} - [{self.images.count()}]"

    async def get_episode_long_image(self, auto_fix: bool = False):
        from apps.tasks import download_image, find_images

        images = await sync_to_async(
            lambda: list(self.images.all().order_by("index").only("image", "id"))
        )()
        if not images:
            if auto_fix:
                find_images.apply_async(args=[self.book.id])
            return

        problem_images = [image for image in images if not image.image]
        if problem_images:
            logger.error(f"Episode {self.id} has missing images")
            if auto_fix:
                for image in problem_images:
                    download_image.apply_async(args=[image.id], countdown=5)
            return

        image_data = (base64.b64decode(item.image) for item in images)
        return await images_to_long_image(image_data)

    async def convert_to_pdf(self, force: bool = False, read: bool = False):
        if self.pdf and not force:
            return await sync_to_async(self.pdf.read)() if read else None

        img = await self.get_episode_long_image(auto_fix=True)
        if not img:
            return

        buffer = await long_image_to_pdf(img, use_process_pool=True)
        await sync_to_async(self.pdf.save)(
            f"{self.title}.pdf", ContentFile(buffer.read())
        )

        return buffer


class Image(models.Model):
    id = models.IntegerField(primary_key=True)
    episode = models.ForeignKey(
        Episode, on_delete=models.CASCADE, related_name="images"
    )
    index = models.IntegerField(default=0)
    image = models.TextField(default="")
    raw_url = models.URLField(default="")

    class Meta:
        verbose_name = "Image"
        verbose_name_plural = "Images"
        ordering = ["episode__id", "index", "id"]

    def __str__(self):
        return f"Image {self.id} for Episode {self.episode.id}"
