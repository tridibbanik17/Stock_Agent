"""
Prepare 5 Chrome Web Store screenshots at 1280x800.
- Combines Email_Header.png + Email_Footer.png into one side-by-side image.
- Resizes the other 4 images individually.
All output uses high-quality Lanczos resampling and is saved at max PNG quality.
"""

from PIL import Image, ImageFilter
import os

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SRC_DIR, "store_ready")
os.makedirs(OUT_DIR, exist_ok=True)

TARGET_W, TARGET_H = 1280, 800
BG_COLOR = (30, 30, 30, 255)  # dark neutral background


def fit_image_on_canvas(img, canvas_w, canvas_h, padding=40):
    """
    Scale img to fit within (canvas_w - 2*padding) x (canvas_h - 2*padding),
    preserving aspect ratio with Lanczos, then center on a canvas.
    """
    max_w = canvas_w - 2 * padding
    max_h = canvas_h - 2 * padding
    scale = min(max_w / img.width, max_h / img.height)
    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (canvas_w, canvas_h), BG_COLOR)
    x = (canvas_w - new_w) // 2
    y = (canvas_h - new_h) // 2
    canvas.paste(resized, (x, y), resized if resized.mode == "RGBA" else None)
    return canvas


def combine_two_side_by_side(img1, img2, canvas_w, canvas_h, gap=40, padding=40):
    """
    Place two images side-by-side on a canvas, each scaled to fit its half.
    """
    half_w = (canvas_w - gap) // 2
    max_h = canvas_h - 2 * padding

    # Scale each image to fit in its half
    def scale_to_fit(img, box_w, box_h):
        s = min((box_w - padding) / img.width, box_h / img.height)
        nw, nh = int(img.width * s), int(img.height * s)
        return img.resize((nw, nh), Image.LANCZOS)

    r1 = scale_to_fit(img1, half_w, max_h)
    r2 = scale_to_fit(img2, half_w, max_h)

    canvas = Image.new("RGBA", (canvas_w, canvas_h), BG_COLOR)

    # Center each in its half
    x1 = (half_w - r1.width) // 2
    y1 = (canvas_h - r1.height) // 2
    canvas.paste(r1, (x1, y1), r1 if r1.mode == "RGBA" else None)

    x2 = half_w + gap + (half_w - r2.width) // 2
    y2 = (canvas_h - r2.height) // 2
    canvas.paste(r2, (x2, y2), r2 if r2.mode == "RGBA" else None)

    return canvas


def save_hq(img, filename):
    """Save as high-quality PNG (lossless). Convert to RGB to drop alpha for store."""
    out_path = os.path.join(OUT_DIR, filename)
    # Chrome Store accepts PNG; keep as RGB for clean display
    rgb = Image.new("RGB", img.size, (30, 30, 30))
    rgb.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
    rgb.save(out_path, "PNG", optimize=False)
    print(f"  Saved: {out_path}  ({rgb.size[0]}x{rgb.size[1]})")


def main():
    print("Preparing Chrome Store screenshots (1280x800)...\n")

    # 1. Combined: Email_Header + Email_Footer
    header = Image.open(os.path.join(SRC_DIR, "Email_Header.png"))
    footer = Image.open(os.path.join(SRC_DIR, "Email_Footer.png"))
    combined = combine_two_side_by_side(header, footer, TARGET_W, TARGET_H)
    save_hq(combined, "01_Email_Report_Preview.png")

    # 2-5. Individual images
    singles = [
        ("Portfolio_Net_Value.png", "02_Portfolio_Net_Value.png"),
        ("Light_Mode_and_Just_Tickers.png", "03_Light_Mode_and_Tickers.png"),
        ("Gemini_Grade_Explanations.png", "04_Gemini_Grade_Explanations.png"),
        ("Email_scheduler_and_Gemini_Key.png", "05_Email_Scheduler_and_Gemini_Key.png"),
    ]

    for src_name, out_name in singles:
        img = Image.open(os.path.join(SRC_DIR, src_name))
        result = fit_image_on_canvas(img, TARGET_W, TARGET_H)
        save_hq(result, out_name)

    print(f"\nDone! All 5 images saved to: {OUT_DIR}")


if __name__ == "__main__":
    main()
