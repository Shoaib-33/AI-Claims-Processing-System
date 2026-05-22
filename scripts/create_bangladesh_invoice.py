from pathlib import Path

import fitz


def main() -> None:
    output = Path("sample_invoice_bangladesh.pdf")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    y = 78
    page.insert_text((190, y), "DHAKA CENTRAL HOSPITAL", fontsize=16, fontname="helv")
    y += 20
    page.insert_text((205, y), "Original Hospital Invoice", fontsize=11, fontname="helv")
    page.draw_line((72, 115), (523, 115), color=(0.55, 0.62, 0.72))

    y = 145
    lines = [
        "Invoice Number: DCH-INV-2024-0718-204",
        "Invoice Date: 18/07/2024",
        "",
        "Patient Information:",
        "- Patient Name: Arafat Rahman",
        "- Date of Birth: 14/03/1995",
        "- Address: House 22, Road 7, Dhanmondi, Dhaka 1205, Bangladesh",
        "- Phone Number: +8801712345678",
        "",
        "Service Details:",
        "- Date of Treatment: 18/07/2024",
        "- Medical Facility: DHAKA CENTRAL HOSPITAL",
        "- Consultant: Dr Nusrat Jahan, General Physician",
        "- Diagnosis: Acute viral fever",
        "- Treatment Type: Outpatient consultation, CBC test, and short-term prescribed medicine",
        "",
        "Service Charges:",
        "- Doctor's consultation fee: 1200",
        "- CBC diagnostic test: 800",
        "- Prescribed medicines: 1500",
        "- Total charge: 3500",
        "- Membership Discount: 5%",
        "- Amount payable: 3325",
        "",
        "Payment Status: Paid by patient",
        "Payment Method: Card",
        "Receipt Status: Original unaltered invoice issued by DHAKA CENTRAL HOSPITAL.",
    ]

    for line in lines:
        if line.endswith(":"):
            y += 14
            page.insert_text((72, y), line, fontsize=12, fontname="helv")
            page.draw_line((72, y + 3), (72 + min(190, len(line) * 7), y + 3))
            y += 24
            continue
        if not line:
            y += 10
            continue
        page.insert_text((72, y), line, fontsize=10.5, fontname="helv")
        y += 20

    page.draw_line((72, 775), (523, 775), color=(0.75, 0.78, 0.82))
    page.insert_text(
        (72, 792),
        "Sample Bangladesh invoice for AI Claims Processing System testing only.",
        fontsize=8,
        fontname="helv",
        color=(0.35, 0.35, 0.35),
    )

    doc.save(output)
    doc.close()
    print(output.resolve())


if __name__ == "__main__":
    main()
