"""Generate Small promo tile (440x280) and Marquee promo tile (1400x560) for Chrome Web Store."""

from PIL import Image, ImageDraw, ImageFont
import os

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Brand colors (matches extension dark theme)
BG_COLOR = (12, 17, 23)  # --bg: #0c1117
ACCENT_COLOR = (47, 156, 240)  # --accent: #2f9cf0
TEXT_COLOR = (233, 238, 244)  # --text: #e9eef4
MUTED_COLOR = (139, 154, 171)  # --muted: #8b9aab
GRADE_GREEN = (62, 207, 142)  # --ok: #3ecf8e
GRADE_YELLOW = (224, 180, 77)  # --warn: #e0b44d
GRADE_RED = (232, 93, 93)  # --error: #e85d5d


def draw_bar_chart_icon(draw, x, y, size):
    """Draw the StockAgent bar chart brand icon."""
    bar_w = size // 5
    gap = size // 10
    bars = [
        (x, y + size * 0.6, bar_w, size * 0.4),
        (x + bar_w + gap, y + size * 0.3, bar_w, size * 0.7),
        (x + 2 * (bar_w + gap), y, bar_w, size),
    ]
    colors = [(ACCENT_COLOR[0], ACCENT_COLOR[1], ACCENT_COLOR[2], 140),
              (ACCENT_COLOR[0], ACCENT_COLOR[1], ACCENT_COLOR[2], 190),
              (ACCENT_COLOR[0], ACCENT_COLOR[1], ACCENT_COLOR[2], 255)]
    for i, (bx, by, bw, bh) in enumerate(bars):
        draw.rounded_rectangle(
            [bx, by, bx + bw, by + bh],
            radius=2,
            fill=colors[i]
        )


def get_font(size):
    """Try system fonts, fall back to default."""
    font_paths = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def get_bold_font(size):
    """Try bold system fonts."""
    font_paths = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return get_font(size)


def generate_small_tile():
    """440x280 small promo tile."""
    w, h = 440, 280
    img = Image.new("RGB", (w, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Subtle gradient overlay
    for y_pos in range(h):
        alpha = int(20 * (1 - y_pos / h))
        draw.line([(0, y_pos), (w, y_pos)], fill=(26, 61, 92, alpha)[:3])

    # Brand icon
    draw_bar_chart_icon(draw, 30, 40, 50)

    # Title
    title_font = get_bold_font(28)
    draw.text((95, 45), "StockAgent", fill=TEXT_COLOR, font=title_font)

    # Tagline
    tag_font = get_font(14)
    draw.text((95, 80), "Privacy-first watchlist grades", fill=MUTED_COLOR, font=tag_font)

    # Grade badges
    badge_font = get_bold_font(13)
    badges = [
        ("STRONG BUY", GRADE_GREEN, 40),
        ("HOLD", GRADE_YELLOW, 180),
        ("AVOID", GRADE_RED, 280),
    ]
    badge_y = 140
    for label, color, bx in badges:
        pw = draw.textlength(label, font=badge_font) + 16
        draw.rounded_rectangle(
            [bx, badge_y, bx + pw, badge_y + 28],
            radius=5,
            fill=(*color, 40)[:3],
            outline=color,
        )
        draw.text((bx + 8, badge_y + 6), label, fill=color, font=badge_font)

    # Bottom tagline
    bottom_font = get_font(12)
    draw.text((40, 200), "Rule-based · Live data · Auto email reports", fill=MUTED_COLOR, font=bottom_font)
    draw.text((40, 222), "US · Canada · India — 25 tickers", fill=MUTED_COLOR, font=bottom_font)

    # Accent line at bottom
    draw.rectangle([0, h - 3, w, h], fill=ACCENT_COLOR)

    path = os.path.join(OUTPUT_DIR, "store_ready", "small_promo_tile_440x280.png")
    img.save(path, "PNG")
    print(f"Created: {path} ({os.path.getsize(path)} bytes)")


def generate_marquee_tile():
    """1400x560 marquee promo tile."""
    w, h = 1400, 560
    img = Image.new("RGB", (w, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Subtle gradient
    for y_pos in range(h):
        r = int(12 + 14 * (1 - y_pos / h))
        g = int(17 + 44 * (1 - y_pos / h))
        b = int(23 + 69 * (1 - y_pos / h))
        draw.line([(0, y_pos), (w, y_pos)], fill=(r, g, b))

    # Brand icon (larger)
    draw_bar_chart_icon(draw, 80, 100, 90)

    # Title
    title_font = get_bold_font(52)
    draw.text((200, 105), "StockAgent", fill=TEXT_COLOR, font=title_font)

    # Subtitle
    sub_font = get_font(22)
    draw.text((200, 170), "Live stock grades, rule-based scoring & auto email reports",
              fill=MUTED_COLOR, font=sub_font)

    # Feature columns
    col_font = get_bold_font(18)
    desc_font = get_font(15)
    features = [
        ("Live Grades", "Real-time data from\nyfinance — scores 0 to 5", ACCENT_COLOR),
        ("Rule-Based", "Transparent metrics:\nD/E, PEG, ROE, SMA, RSI", GRADE_GREEN),
        ("Auto Reports", "Scheduled email digests\non your days & times", GRADE_YELLOW),
        ("Privacy-First", "Holdings never leave\nyour device — period", (180, 160, 255)),
    ]

    col_w = 280
    col_start_x = 80
    col_y = 280

    for i, (title, desc, color) in enumerate(features):
        cx = col_start_x + i * (col_w + 40)
        # Color dot
        draw.ellipse([cx, col_y + 4, cx + 12, col_y + 16], fill=color)
        draw.text((cx + 20, col_y), title, fill=TEXT_COLOR, font=col_font)
        draw.text((cx + 20, col_y + 30), desc, fill=MUTED_COLOR, font=desc_font)

    # Grade badges on the right side
    badge_font = get_bold_font(16)
    badges = [
        ("STRONG BUY (4/5)", GRADE_GREEN),
        ("HOLD (3/5)", GRADE_YELLOW),
        ("AVOID (1/5)", GRADE_RED),
    ]
    badge_x = 1050
    badge_y_start = 300

    for i, (label, color) in enumerate(badges):
        by = badge_y_start + i * 50
        pw = draw.textlength(label, font=badge_font) + 20
        draw.rounded_rectangle(
            [badge_x, by, badge_x + pw, by + 34],
            radius=6,
            fill=(*color, 30)[:3],
            outline=color,
        )
        draw.text((badge_x + 10, by + 8), label, fill=color, font=badge_font)

    # Bottom bar
    draw.rectangle([0, h - 4, w, h], fill=ACCENT_COLOR)

    # Bottom text
    bottom_font = get_font(14)
    draw.text((80, h - 40), "US (NASDAQ/NYSE) · Canada (TSX) · India (NSE/BSE) — up to 25 tickers",
              fill=MUTED_COLOR, font=bottom_font)

    path = os.path.join(OUTPUT_DIR, "store_ready", "marquee_promo_tile_1400x560.png")
    img.save(path, "PNG")
    print(f"Created: {path} ({os.path.getsize(path)} bytes)")


if __name__ == "__main__":
    os.makedirs(os.path.join(OUTPUT_DIR, "store_ready"), exist_ok=True)
    generate_small_tile()
    generate_marquee_tile()
    print("\nDone! Upload these to Chrome Web Store.")
