import frappe
from frappe.utils import nowdate

def on_delivery_note_submit(doc, method):
    try:
        # Fetch the Sales Order linked to the Delivery Note
        sales_order = frappe.get_doc("Sales Order", doc.items[0].against_sales_order) if doc.items[0].against_sales_order else None

        # Create Sales Invoice
        sales_invoice = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": doc.customer,
            "posting_date": nowdate(),
            "due_date": nowdate(),
            "debit_to": "1310 - Debtors - CMJ",  # Replace with your debtor account
            "items": []
        })

        # Add items from the Delivery Note to the Sales Invoice and link Delivery Note and Sales Order
        for item in doc.items:
            sales_invoice.append("items", {
                "item_code": item.item_code,
                "qty": item.qty,
                "rate": item.rate,
                "amount": item.amount,
                "warehouse": item.warehouse,
                "delivery_note": doc.name,  # Link to Delivery Note
                "sales_order": sales_order.name if sales_order else None  # Link to Sales Order if available
            })

        # Copy taxes from the Delivery Note to the Sales Invoice
        for tax in doc.taxes:
            sales_invoice.append("taxes", {
                "charge_type": tax.charge_type,
                "account_head": tax.account_head,
                "description": tax.description,
                "rate": tax.rate,
                "tax_amount": tax.tax_amount,
                "cost_center": tax.cost_center,
                "delivery_note": doc.name,
                "sales_order": sales_order.name if sales_order else None
            })

        # Insert and Submit the Sales Invoice
        sales_invoice.insert(ignore_permissions=True)
        sales_invoice.submit()

        # Link the Payment Entry to the Sales Invoice if payment was made online earlier
        payment_entries = frappe.get_all("Payment Entry Reference", filters={
            "reference_name": sales_order.name,
            "docstatus": 1
        }, fields=["parent"])
        
        # Update each Payment Entry to allocate payment to the Sales Invoice
        if payment_entries:
            for pe in payment_entries:
                payment_entry_doc = frappe.get_doc("Payment Entry", pe.get("parent"))
                link_payment_entry_to_sales_invoice(pe.get("parent"), sales_invoice.name, payment_entry_doc.paid_amount)
                
                # payment_entry_doc.append("references", {
                #     "reference_doctype": "Sales Invoice",
                #     "reference_name": sales_invoice.name,
                #     "allocated_amount": sales_invoice.outstanding_amount
                # })
                # payment_entry_doc.save(ignore_permissions=True)

        # Update Delivery Note status to 'Completed'
        doc.db_set("per_billed", 100)
        doc.db_set("status", "Completed")

        sales_order.db_set("per_billed", 100)

        frappe.msgprint(f"Sales Invoice {sales_invoice.name} created and linked successfully.")

    except Exception as e:
        frappe.throw(f"Error while creating Sales Invoice: {str(e)}")


def link_payment_entry_to_sales_invoice(payment_entry_name, sales_invoice_name, amount):
    try:
        # Temporarily bypass permission checks
        frappe.flags.ignore_permissions = True

        # Fetch and cancel the payment entry
        payment_entry = frappe.get_doc("Payment Entry", payment_entry_name)
        
        # Set ignore_permissions and cancel without additional arguments
        payment_entry.flags.ignore_permissions = True
        payment_entry.cancel()  # No arguments for cancel()

        # Amend the payment entry to add the new Sales Invoice reference
        amended_payment_entry = frappe.copy_doc(payment_entry)
        amended_payment_entry.docstatus = 0  # Set to draft before submission

        # Clear existing references
        amended_payment_entry.references = []

        amended_payment_entry.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": sales_invoice_name,
            "allocated_amount": amount
        })

        # Insert and submit the amended Payment Entry
        amended_payment_entry.insert(ignore_permissions=True)
        amended_payment_entry.submit()

        frappe.msgprint(f"Payment Entry {amended_payment_entry.name} linked with Sales Invoice {sales_invoice_name}.")

    except frappe.PermissionError:
        frappe.throw("Insufficient permissions to cancel or amend Payment Entry.")
    
    except Exception as e:
        frappe.throw(f"Could not link Payment Entry: {str(e)}")

    finally:
        # Revert ignore permissions flag
        frappe.flags.ignore_permissions = False




