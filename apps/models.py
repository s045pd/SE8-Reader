# Import necessary modules
import base64
import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import models
from PIL import Image as PILImage
from PIL import ImageFile
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# Set up logging
logger = logging.getLogger(__name__)

# Allow loading of truncated images
ImageFile.LOAD_TRUNCATED_IMAGES = True


# Define Tag model
class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="tag-name")

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["name"]

    def __str__(self):
        return self.name


# Define Book model
class Book(models.Model):
    # Book fields
    id = models.CharField(
        max_length=100,
        verbose_name="book-ID",
        unique=True,
        db_index=True,
        help_text="ID of the book",
        default="",
        primary_key=True,
    )
    image = models.TextField(
        verbose_name="book-image",
        help_text="Image of the book",
        default="",
    )
    hot = models.IntegerField(
        verbose_name="book-hot",
        help_text="Hot of the book",
        default=0,
    )
    title = models.CharField(
        max_length=100,
        verbose_name="book-title",
        help_text="Title of the book",
        default="",
    )
    tags = models.ManyToManyField(Tag, related_name="books")
    description = models.TextField(
        verbose_name="book-description",
        help_text="Description of the book",
        default="",
    )
    raw_url = models.URLField(
        verbose_name="book-raw-url",
        help_text="Raw URL of the book",
        default="",
    )
    image_url = models.URLField(
        verbose_name="book-image-url",
        help_text="Image URL of the book",
        default="",
    )

    class Meta:
        verbose_name = "Book"
        verbose_name_plural = "Books"
        ordering = ["title"]

    def __str__(self):
        return f"<{self.title} [{self.episodes.count()}]>"

    def is_outdated(self, episodes_title: str) -> bool:
        """
        Check if the book is outdated based on the latest episode title
        """
        if not self.episodes.exists():
            return True

        return self.episodes.all().order_by("id").last().title != episodes_title


# Define Episode model
class Episode(models.Model):
    id = models.IntegerField(primary_key=True, verbose_name="episode-ID")
    title = models.CharField(max_length=100, verbose_name="episode-title", default="")
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="episodes")
    raw_url = models.URLField(verbose_name="episode-raw-url", default="")
    pdf = models.FileField(
        upload_to="pdfs", verbose_name="episode-pdf", null=True, blank=True
    )

    class Meta:
        verbose_name = "Episode"
        verbose_name_plural = "Episodes"
        ordering = [
            "book__id",
            "id",
            "title",
        ]

    def __str__(self):
        return f"{self.book.title} - {self.id} - [{self.images.count()}]"

    def get_episode_long_image(self, auto_fix: bool = False):
        """
        Get the long image of the episode by combining all individual images
        """
        from apps.tasks import download_image, find_images

        # Get all images for this episode
        images = self.images.all().order_by("index").only("image", "id")
        if not images.exists():
            if auto_fix:
                find_images.apply_async(args=[self.book.id])
            return

        # Check for missing images and attempt to fix if auto_fix is True
        if (problem_images := images.filter(image="")).exists():
            logger.error(f"Episode {self.id} has missing images")
            if auto_fix:
                for image in problem_images.only("id").iterator():
                    download_image.apply_async(args=[image.id], countdown=5)
            return

        # Process and combine images
        image_list = []
        total_height = 0
        max_width = 0

        for image in images.iterator():
            try:
                decoded_image = base64.b64decode(image.image)
                img = PILImage.open(BytesIO(decoded_image))

                if img.mode != "RGB":
                    img = img.convert("RGB")

                image_list.append(img)
                total_height += img.height
                max_width = max(max_width, img.width)
            except Exception as e:
                logger.error(f"Error loading image {image.id}: {str(e)}")
                return

        # Combine all images into one long image
        combined_image = PILImage.new("RGB", (max_width, total_height))
        y_offset = 0
        for img in image_list:
            combined_image.paste(img, (0, y_offset))
            y_offset += img.height

        return combined_image

    def convert_to_pdf(self, force: bool = False, read: bool = False):
        """
        Convert the episode's long image to a PDF
        """
        # Return existing PDF if available and not forced to recreate
        if self.pdf and not force:
            return self.pdf.read() if read else None

        # Get the long image
        if not (img := self.get_episode_long_image(auto_fix=True)):
            return

        # Calculate dimensions and scaling
        img_width, img_height = img.size
        pdf_width, pdf_height = A4
        scale = pdf_width / img_width
        scaled_height = int(img_height * scale)
        pages = (scaled_height + int(pdf_height) - 1) // int(pdf_height)

        # Create PDF
        buffer = BytesIO()
        pdf_canvas = canvas.Canvas(buffer, pagesize=A4)

        for page in range(pages):
            # Calculate crop box for each page
            top = int(page * pdf_height / scale)
            bottom = int((page + 1) * pdf_height / scale)
            bottom = min(bottom, img_height)

            if top >= bottom:
                logger.error(
                    f"Invalid crop box coordinates: top={top}, bottom={bottom}"
                )
                continue

            # Crop and resize image for the current page
            crop_box = (0, top, img_width, bottom)
            cropped_img = img.crop(crop_box)

            new_width = int(pdf_width)
            new_height = int(cropped_img.height * scale)

            if new_width <= 0 or new_height <= 0:
                logger.error(
                    f"Invalid dimensions for resized image: width={new_width}, height={new_height}. "
                    f"Original crop box: top={top}, bottom={bottom}, "
                    f"cropped_img.height={cropped_img.height}, scale={scale}"
                )
                continue

            cropped_img = cropped_img.resize((new_width, new_height))

            # Save cropped image to buffer and draw on PDF
            img_buffer = BytesIO()
            cropped_img.save(img_buffer, format="PNG")
            img_buffer.seek(0)

            pdf_canvas.drawImage(
                ImageReader(img_buffer),
                0,
                pdf_height - cropped_img.height,
                width=pdf_width,
                height=cropped_img.height,
            )

            pdf_canvas.showPage()

        pdf_canvas.save()

        # Save PDF to model
        buffer.seek(0)
        self.pdf.save(f"{self.title}.pdf", ContentFile(buffer.read()))
        self.save(update_fields=["pdf"])

        return buffer


# Define Image model
class Image(models.Model):
    id = models.IntegerField(primary_key=True, verbose_name="image-ID")
    episode = models.ForeignKey(
        Episode, on_delete=models.CASCADE, related_name="images"
    )
    index = models.IntegerField(verbose_name="image-index", default=0)
    image = models.TextField(verbose_name="image", default="")
    raw_url = models.URLField(verbose_name="image-raw-url", default="")

    class Meta:
        verbose_name = "Image"
        verbose_name_plural = "Images"
        ordering = [
            "episode__id",
            "index",
            "id",
        ]

    def __str__(self):
        return f"Image {self.id} for Episode {self.episode.id}"
