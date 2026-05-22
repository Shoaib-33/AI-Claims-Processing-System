from pathlib import Path

import fitz


def main() -> None:
    output = Path("sample_invoice_apollo.pdf")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    y = 90
    page.insert_text((230, y), "APOLLO HOSPITALS", fontsize=16, fontname="helv")

    y += 45
    page.insert_text((80, y), "Patient Information:", fontsize=12, fontname="helv")
    page.draw_line((80, y + 3), (200, y + 3))

    y += 35
    lines = [
        "- Name: Surbhit",
        "- Date of Birth: 01/01/1998",
        "- Address: India",
        "- Phone Number: 1239874653",
        "",
        "Service Details:",
        "Date of Service: 02/02/2024",
        "Diagnosis: Headache",
        "",
        "Details: Consultation and medicines as mentioned in the prescription",
        "",
        "Service charges:",
        "Doctor's fee - 1500",
        "Medicines - 2000",
        "",
        "Total charge - 3500",
        "Membership Discount - 10%",
        "Amount payable - 3150",
    ]

    for line in lines:
        if line == "Service Details:":
            y += 18
            page.insert_text((80, y), line, fontsize=12, fontname="helv")
            page.draw_line((80, y + 3), (170, y + 3))
            y += 24
            continue

        page.insert_text((80, y), line, fontsize=11, fontname="helv")
        y += 22

    page.insert_text(
        (80, 760),
        "This is a sample invoice for testing the AI Claims Processing System.",
        fontsize=9,
        fontname="helv",
        color=(0.35, 0.35, 0.35),
    )

    doc.save(output)
    doc.close()
    print(output.resolve())


if __name__ == "__main__":
    main()
