from pathlib import Path

import fitz


OUTPUT = Path("complete_claim_package_surbhit.pdf")


def add_heading(page: fitz.Page, title: str) -> int:
    page.insert_text((72, 58), title, fontsize=17, fontname="helv")
    page.draw_line((72, 82), (523, 82), color=(0.55, 0.62, 0.72))
    return 112


def add_lines(page: fitz.Page, lines: list[str], y: int, size: int = 10) -> int:
    for line in lines:
        if not line:
            y += 14
            continue
        page.insert_text((72, y), line, fontsize=size, fontname="helv")
        y += 18
    return y


def add_footer(page: fitz.Page, page_no: int) -> None:
    page.draw_line((72, 775), (523, 775), color=(0.75, 0.78, 0.82))
    page.insert_text(
        (72, 792),
        f"Sample document for AI Claims Processing System testing only | Page {page_no}",
        fontsize=8,
        fontname="helv",
        color=(0.35, 0.35, 0.35),
    )


def main() -> None:
    doc = fitz.open()

    page = doc.new_page(width=595, height=842)
    y = add_heading(page, "APOLLO HOSPITALS - ORIGINAL TAX INVOICE")
    y = add_lines(
        page,
        [
            "Invoice Number: AH-INV-2024-0202-115",
            "Pre-authorisation Number: BUPA-PA-240202-7718",
            "Invoice Date: 02/02/2024",
            "",
            "Patient Information:",
            "- Patient Name: Surbhit",
            "- Date of Birth: 01/01/1998",
            "- Address: 22 Baker Street, London, United Kingdom",
            "- Phone Number: +44 7700 900123",
            "- Policy Number: BUPA-HLT-204455",
            "",
            "Service Details:",
            "- Date of Treatment: 02/02/2024",
            "- Medical Facility: APOLLO HOSPITALS",
            "- Consultant: Dr Amelia Wright, General Practitioner",
            "- Diagnosis: Acute tension headache",
            "- Treatment Type: Outpatient consultation and short-term prescribed medicine",
            "",
            "Service Charges:",
            "- Doctor's consultation fee: 1500",
            "- Diagnostic observation and clinical assessment: 500",
            "- Prescribed medicines: 1500",
            "- Total charge: 3500",
            "- Membership Discount: 10%",
            "- Amount payable: 3150",
            "",
            "Payment Status: Paid by patient",
            "Receipt Status: Original unaltered invoice issued by APOLLO HOSPITALS.",
        ],
        y,
    )
    add_footer(page, 1)

    page = doc.new_page(width=595, height=842)
    y = add_heading(page, "PRESCRIPTION")
    y = add_lines(
        page,
        [
            "Prescription ID: RX-2024-0202-883",
            "Date: 02/02/2024",
            "Patient Name: Surbhit",
            "Diagnosis: Acute tension headache",
            "",
            "Prescribed Medicines:",
            "1. Paracetamol 500mg - Take one tablet every 6 hours if required for pain, maximum 3 days.",
            "2. Ibuprofen 200mg - Take one tablet after food if required, maximum 2 days.",
            "3. Oral rehydration solution - As required.",
            "",
            "Clinical Note:",
            "Medication was prescribed for short-term symptom relief linked to the acute outpatient visit.",
            "No long-term medicine, chronic disease management, or preventive screening was prescribed.",
            "",
            "Prescriber:",
            "Dr Amelia Wright",
            "GMC Number: 7123456",
            "Signature: Dr Amelia Wright",
        ],
        y,
    )
    add_footer(page, 2)

    page = doc.new_page(width=595, height=842)
    y = add_heading(page, "MEDICAL REPORT")
    y = add_lines(
        page,
        [
            "Report Number: MR-2024-0202-491",
            "Date of Report: 02/02/2024",
            "Patient Name: Surbhit",
            "Date of Treatment: 02/02/2024",
            "Treating Clinician: Dr Amelia Wright",
            "",
            "Presenting Complaint:",
            "The patient attended outpatient consultation with headache symptoms that started the same day.",
            "",
            "Clinical Findings:",
            "- No loss of consciousness reported.",
            "- No neurological deficit observed during examination.",
            "- Blood pressure and temperature were within normal limits.",
            "- No evidence of chronic headache disorder was recorded.",
            "",
            "Diagnosis:",
            "Acute tension headache.",
            "",
            "Treatment Provided:",
            "Outpatient consultation, clinical assessment, advice on hydration/rest, and short-term medication.",
            "",
            "Medical Necessity Statement:",
            "The consultation was medically necessary to assess acute symptoms and rule out urgent warning signs.",
            "The treatment was short-term and intended to return the patient to their prior state of health.",
        ],
        y,
    )
    add_footer(page, 3)

    page = doc.new_page(width=595, height=842)
    y = add_heading(page, "BUPA PRE-AUTHORISATION CONFIRMATION")
    y = add_lines(
        page,
        [
            "Pre-authorisation Number: BUPA-PA-240202-7718",
            "Date Issued: 02/02/2024",
            "Policy Number: BUPA-HLT-204455",
            "Patient Name: Surbhit",
            "",
            "Authorised Service:",
            "- Outpatient consultation for acute symptoms",
            "- Consultant/GP assessment",
            "- Clinically necessary short-term treatment linked to the consultation",
            "",
            "Facility and Clinician:",
            "- Medical Facility: APOLLO HOSPITALS",
            "- Clinician: Dr Amelia Wright",
            "",
            "Important Note:",
            "This sample confirmation is provided for testing the AI workflow only.",
            "Final payment remains subject to policy terms, membership certificate benefits, excess, allowances,",
            "recognised provider status, and review of original documents.",
        ],
        y,
    )
    add_footer(page, 4)

    page = doc.new_page(width=595, height=842)
    y = add_heading(page, "PAYMENT RECEIPT")
    y = add_lines(
        page,
        [
            "Receipt Number: PAY-2024-0202-3150",
            "Invoice Number: AH-INV-2024-0202-115",
            "Payment Date: 02/02/2024",
            "Patient Name: Surbhit",
            "Medical Facility: APOLLO HOSPITALS",
            "",
            "Payment Breakdown:",
            "- Total charge: 3500",
            "- Membership discount: 350",
            "- Amount payable: 3150",
            "- Amount paid: 3150",
            "",
            "Payment Method: Card",
            "Payment Status: Paid in full",
            "Issued by: APOLLO HOSPITALS Billing Department",
        ],
        y,
    )
    add_footer(page, 5)

    doc.save(OUTPUT)
    doc.close()
    print(OUTPUT.resolve())


if __name__ == "__main__":
    main()
