import frappe
from frappe import _
# your_custom_app/your_custom_app/utils.py

# HTTP Status Codes as constants for easy access
class HTTPStatus:
    OK = 200
    CREATED = 201
    NO_CONTENT = 204
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    METHOD_NOT_ALLOWED = 405
    CONFLICT = 409
    INTERNAL_SERVER_ERROR = 500
    NOT_IMPLEMENTED = 501
    BAD_GATEWAY = 502
    SERVICE_UNAVAILABLE = 503
    GATEWAY_TIMEOUT = 504

def validate_coupon_against_cart(quotation, coupon_name):
    # Ensure the Quotation has a coupon code
    if quotation.coupon_code:
        frappe.throw(_("This cart already have a coupon code!"))

    # Fetch the Pricing Rule linked to the Coupon Code
    coupon_details = frappe.db.get_value("Coupon Code", coupon_name, ["coupon_code","pricing_rule"], as_dict=True)
    
    if not coupon_details:
        frappe.throw(f"No pricing rule associated with coupon code: {coupon_name}")
    
    # Fetch the Pricing Rule details (optional)
    pricing_rule = frappe.db.get_value(
        "Pricing Rule",
        coupon_details.get("pricing_rule"),
        ["name", "min_qty", "min_amt"],
        as_dict=True
    )

    if not pricing_rule:
        frappe.throw(
            f"Invalid coupon code: {coupon_details.get('coupon_code')}."
        )

    # Validate minimum quantity
    total_quantity = sum([item.qty for item in quotation.items])
    if pricing_rule.min_qty and total_quantity < pricing_rule.min_qty:
        frappe.throw(
            f"Coupon code '{coupon_details.get('coupon_code')}' requires a minimum quantity of {pricing_rule.min_qty}."
        )

    # Validate minimum amount
    if pricing_rule.min_amt and quotation.net_total <= pricing_rule.min_amt:
        frappe.throw(
            f"Coupon code '{coupon_details.get('coupon_code')}' requires a minimum amount of {frappe.format_value(pricing_rule.min_amt, 'Currency')}."
        )
