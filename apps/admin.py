from django.contrib import admin
from django.utils.html import format_html

from apps.models import Book, Episode, Image, Tag
from apps.tasks import convert_to_pdf, download_image, find_episodes


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "get_book_count")

    def get_book_count(self, obj):
        """Get the number of books associated with this tag"""
        return obj.books.count()

    get_book_count.short_description = "Number of Books"


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "get_episode_count", "hot", "view_episodes")
    search_fields = ("title", "id")
    list_filter = ("tags",)
    readonly_fields = (
        "title",
        "id",
        "raw_url",
        "image_url",
        "image",
        "description",
        "hot",
        "tags",
    )
    actions = ["start_crawling"]

    def get_episode_count(self, obj):
        """Get the number of episodes for this book"""
        return obj.episodes.count()

    get_episode_count.short_description = "Number of Episodes"

    def view_episodes(self, obj):
        """Generate a link to view episodes of this book"""
        return format_html(
            '<a class="button" href="{}">Read</a>',
            f"/admin/apps/episode/?book__id__exact={obj.id}",
        )

    view_episodes.short_description = "View Episodes"

    def start_crawling(self, request, queryset):
        """Start crawling episodes for selected books"""
        for book in queryset:
            find_episodes.apply_async(args=[book.id])

    start_crawling.short_description = "Start Crawling"


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "book",
        "get_image_count",
        "view_images",
        "all_images",
        "has_pdf",
        "read_episode",
    )
    search_fields = ("title", "book__title")
    list_filter = ("book__tags", "book__title")
    readonly_fields = ("book", "title", "id", "raw_url")
    actions = ["get_images", "convert_to_pdf", "convert_to_pdf_force", "refresh_images"]

    def get_image_count(self, obj):
        """Get the count of images for this episode"""
        return f'{obj.images.exclude(image="").count()}/{obj.images.count()}'

    get_image_count.short_description = "Number of Images"

    def read_episode(self, obj):
        """Generate a link to read this episode"""
        return format_html(
            '<a class="button" href="{}">Read</a>', f"/api/episode/{obj.id}/"
        )

    read_episode.short_description = "Read"

    def all_images(self, obj):
        """Check if all images for this episode are present"""
        return not obj.images.filter(image="").exists()

    all_images.short_description = "All Images"
    all_images.boolean = True

    def has_pdf(self, obj):
        """Check if this episode has a PDF"""
        return bool(obj.pdf)

    has_pdf.short_description = "Has PDF"
    has_pdf.boolean = True

    def view_images(self, obj):
        """Generate a link to view images of this episode"""
        return format_html(
            '<a class="button" href="{}">View</a>',
            f"/admin/apps/image/?episode__id__exact={obj.id}",
        )

    view_images.short_description = "View Images"

    def convert_to_pdf(self, request, queryset):
        """Convert selected episodes to PDF"""
        for episode in queryset:
            convert_to_pdf.apply_async(args=[episode.id])

    convert_to_pdf.short_description = "Convert to PDF"

    def convert_to_pdf_force(self, request, queryset):
        """Force convert selected episodes to PDF"""
        for episode in queryset:
            convert_to_pdf.apply_async(args=[episode.id, True])

    convert_to_pdf_force.short_description = "Convert to PDF (Force)"

    def get_images(self, request, queryset):
        """Download images for selected episodes"""
        for episode in queryset:
            for image in episode.images.all():
                download_image.apply_async(args=[image.id])

    get_images.short_description = "Get Images"

    def refresh_images(self, request, queryset):
        """Refresh images for selected episodes"""
        for episode in queryset:
            find_episodes.apply_async(args=[episode.id])

    refresh_images.short_description = "Refresh Images"


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ("id", "episode", "index", "get_image_display")
    search_fields = ("episode__title", "id")
    list_filter = ("episode__book",)
    readonly_fields = ("episode", "index", "id", "raw_url", "image")
    actions = ["refresh_image"]

    def get_image_display(self, obj):
        """Display the image in the admin panel"""
        try:
            return format_html(
                f'<img src="data:image/jpeg;base64,{obj.image}" width="100" height="100"/>',
            )
        except Exception as e:
            return str(e)

    get_image_display.short_description = "Image"

    def episode_link(self, obj):
        """Generate a link to view the episode for this image"""
        return format_html(
            '<a href="{}">{}</a>', obj.episode.get_admin_url(), obj.episode.title
        )

    episode_link.short_description = "Episode"

    def get_queryset(self, request):
        """Optimize queryset by selecting related episode"""
        queryset = super().get_queryset(request)
        queryset = queryset.select_related("episode")
        return queryset

    def refresh_image(self, request, queryset):
        """Refresh image for selected images"""
        for image in queryset:
            download_image.apply_async(args=[image.id])

    refresh_image.short_description = "Refresh Image"
