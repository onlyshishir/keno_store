from erpnext.selling.doctype import quotation
import frappe
from frappe.utils import money_in_words

def validate_coupon_on_cart_update(doc, method):
    # Ensure the Quotation has a coupon code
    if not doc.coupon_code:
        return

    # Fetch the Pricing Rule linked to the Coupon Code
    pricing_rule_name = frappe.db.get_value("Coupon Code", doc.coupon_code, "pricing_rule")
    
    if not pricing_rule_name:
        frappe.throw(f"No pricing rule associated with coupon code: {doc.coupon_code}")
    
    # Fetch the Pricing Rule details (optional)
    pricing_rule = frappe.db.get_value(
        "Pricing Rule",
        pricing_rule_name,
        ["name", "min_qty", "min_amt"],
        as_dict=True
    )

    # Fetch the associated Pricing Rule for the coupon code
    # pricing_rule = frappe.db.get_value(
    #     "Pricing Rule",
    #     {"coupon_code": doc.coupon_code},
    #     ["name", "min_qty", "min_amt"],
    #     as_dict=True
    # )

    if not pricing_rule:
        frappe.msgprint(
            f"Invalid coupon code: {doc.coupon_code}. The coupon has been removed.",
            indicator="orange"
        )
        doc.coupon_code = None
        return

    # Validate minimum quantity
    total_quantity = sum([item.qty for item in doc.items])
    if pricing_rule.min_qty and total_quantity < pricing_rule.min_qty:
        frappe.msgprint(
            f"Coupon code '{doc.coupon_code}' requires a minimum quantity of {pricing_rule.min_qty}. It has been removed.",
            indicator="orange"
        )
        # Reset additional discount fields
        doc.additional_discount_percentage = None
        doc.coupon_code = None
        doc.base_discount_amount = 0
        doc.base_net_total = doc.base_net_total + doc.discount_amount
        doc.base_grand_total = doc.base_grand_total + doc.discount_amount
        doc.net_total = doc.net_total + doc.discount_amount
        doc.grand_total = doc.grand_total + doc.discount_amount
        doc.in_words = money_in_words(doc.grand_total, "USD")
        doc.discount_amount = None
        return

    # Validate minimum amount
    if pricing_rule.min_amt and doc.net_total <= pricing_rule.min_amt:
        frappe.msgprint(
            f"Coupon code '{doc.coupon_code}' requires a minimum amount of {frappe.format_value(pricing_rule.min_amt, 'Currency')}. It has been removed.",
            indicator="orange"
        )
        # Reset additional discount fields
        doc.additional_discount_percentage = None
        doc.coupon_code = None
        doc.base_discount_amount = 0
        doc.base_net_total = doc.base_net_total + doc.discount_amount
        doc.base_grand_total = doc.base_grand_total + doc.discount_amount
        doc.net_total = doc.net_total + doc.discount_amount
        doc.grand_total = doc.grand_total + doc.discount_amount
        doc.in_words = money_in_words(doc.grand_total, "USD")
        doc.discount_amount = None
        return