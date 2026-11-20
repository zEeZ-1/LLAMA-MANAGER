"""One-shot bounded image validator. No model/framework imports."""

import json
import resource
import shutil
import sys
import warnings
from pathlib import Path


def main():
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024**2, 384 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    from PIL import Image, ImageOps

    cfg = json.load(sys.stdin)
    Image.MAX_IMAGE_PIXELS = cfg["max_pixels"]
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    source = Path(cfg["source"])
    output = Path(cfg["output"])
    thumb = Path(cfg["thumbnail"])
    with Image.open(source) as image:
        if (
            image.format not in {"JPEG", "PNG", "WEBP"}
            or getattr(image, "n_frames", 1) != 1
        ):
            raise ValueError("Only static JPEG, PNG and WebP are supported")
        width, height = image.size
        if width * height > cfg["max_pixels"] or max(width, height) > cfg["max_side"]:
            raise ValueError("Image dimensions exceed the limit")
        image.load()
        scaled = False
        if cfg["normalized"]:
            if (
                image.format not in {"JPEG", "PNG"}
                or max(width, height) > cfg["normalized_side"]
            ):
                raise ValueError("Invalid normalized image")
            if (
                image.getexif()
                or any(
                    k in image.info for k in ("icc_profile", "xmp", "XML:com.adobe.xmp")
                )
                or getattr(image, "text", {})
            ):
                raise ValueError("Normalized image must not contain private metadata")
            suffix = ".jpg" if image.format == "JPEG" else ".png"
            result = output.with_suffix(suffix)
            shutil.copyfile(source, result)
            preview = image.copy()
        else:
            picture = ImageOps.exif_transpose(image)
            oriented_size = picture.size
            picture.thumbnail((cfg["normalized_side"], cfg["normalized_side"]))
            scaled = picture.size != oriented_size
            has_alpha = "A" in picture.getbands() or "transparency" in picture.info
            suffix = ".png" if has_alpha or image.format == "PNG" else ".jpg"
            result = output.with_suffix(suffix)
            picture = picture.convert("RGBA" if has_alpha else "RGB")
            picture.info.clear()
            picture.save(
                result, format="PNG" if suffix == ".png" else "JPEG", quality=90
            )
            width, height = picture.size
            preview = picture
        if result.stat().st_size > cfg["normalized_bytes"]:
            raise ValueError("Normalized image exceeds file limit")
        preview.thumbnail((320, 320))
        if "A" in preview.getbands():
            background = Image.new("RGB", preview.size, "white")
            background.paste(preview, mask=preview.getchannel("A"))
            preview = background
        preview.convert("RGB").save(thumb, format="JPEG", quality=75)
        if thumb.stat().st_size > 128 * 1024:
            raise ValueError("Thumbnail exceeds file limit")
    print(
        json.dumps(
            dict(
                suffix=suffix,
                mime_type="image/png" if suffix == ".png" else "image/jpeg",
                width=width,
                height=height,
                scaled=scaled,
            )
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Image format, dimensions or encoded data are invalid", file=sys.stderr)
        sys.exit(1)
