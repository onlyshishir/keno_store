import frappe
import pyqrcode
from io import BytesIO
from frappe.utils.file_manager import save_file

@frappe.whitelist()
def generate_qr(docname):
    """Generate a QR Code and attach it to the Delivery Note"""
    doc = frappe.get_doc("Delivery Note", docname)

    first_sales_order = next((item.against_sales_order for item in doc.items if item.against_sales_order), None)

    qr_data = f"Sales Order: {first_sales_order}\nDelivery Note: {doc.name}\nCustomer: {doc.customer_name}\nContact: {doc.contact_display}\nTotal: {doc.grand_total}"
    # qr_data = f"Delivery Note: {doc.name}\nCustomer: {doc.customer_name}\nContact: {doc.contact_display}\nTotal: {doc.grand_total}"

    # Generate QR Code
    qr = pyqrcode.create(qr_data)

    # Save QR Code as PNG in memory
    buffer = BytesIO()
    qr.png(buffer, scale=6)
    
    # Save the QR Code as an attachment
    file_name = f"QR_{docname}.png"
    file_doc = save_file(file_name, buffer.getvalue(), "Delivery Note", docname, is_private=0)

    return file_doc.file_url  # Return file URL to be used in print format
