import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from io import BytesIO
from logging import getLogger
from subprocess import DEVNULL, PIPE, Popen

from PIL import Image as PILImage
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

logger = getLogger(__name__)


def load_and_convert_image(image):
    try:
        img = PILImage.open(BytesIO(image))
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception as e:
        logger.error(f"Error loading image: {str(e)}")
        return None


def combine_images(images):
    # Process and combine images
    image_list = []
    total_height = 0
    max_width = 0

    for image in images:
        img = load_and_convert_image(image)
        if img is not None:
            image_list.append(img)
            total_height += img.height
            max_width = max(max_width, img.width)

    # Combine all images into one long image
    combined_image = PILImage.new("RGB", (max_width, total_height))
    y_offset = 0
    for img in image_list:
        combined_image.paste(img, (0, y_offset))
        y_offset += img.height

    return combined_image


def create_pdf(img):
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
            logger.error(f"Invalid crop box coordinates: top={top}, bottom={bottom}")
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

    return buffer


async def images_to_long_image(images, use_process_pool=False):
    loop = asyncio.get_event_loop()
    if use_process_pool:
        with ProcessPoolExecutor() as executor:
            return await loop.run_in_executor(executor, combine_images, images)
    else:
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, combine_images, images)


async def long_image_to_pdf(img, use_process_pool=False):
    loop = asyncio.get_event_loop()
    if use_process_pool:
        with ProcessPoolExecutor() as executor:
            return await loop.run_in_executor(executor, create_pdf, img)
    else:
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, create_pdf, img)


def run_cmd(code, sync: bool = True, shell=True) -> None | str | bytes:
    p = Popen(
        code,
        shell=shell,
        **(
            {"stdout": PIPE, "stderr": PIPE}
            if sync
            else {
                "stdin": None,
                "stdout": DEVNULL,
                "stderr": DEVNULL,
                "close_fds": True,
            }
        ),
    )
    logger.debug(f"[PID:{p.pid} Sync:{sync}]\t{code}")
    if not sync:
        return
    stdout, stderr = list(map(bytes.decode, p.communicate()))
    if stderr:
        logger.error(stderr)
    logger.debug(stdout)
    return stdout


def curl(url: str) -> None:
    return run_cmd(
        code=f'curl -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:91.0) Gecko/20100101 Firefox/91.0" -H "Accept-Language: en-GB,en;q=0.9,zh-CN;q=0.8,zh;q=0.7" -H "Cache-Control: max-age=0" -H "Dnt: 1" -H "Priority: u=0, i" {url}',
        sync=True,
    )
