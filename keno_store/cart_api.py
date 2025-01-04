# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import calendar
from http import HTTPStatus
import frappe
from frappe.auth import validate_auth_via_api_keys
from frappe.model.docstatus import DocStatus
from frappe.utils.data import add_days, getdate, now, today
import requests
import stripe
import frappe.defaults
from frappe import _, throw
from frappe.contacts.doctype.address.address import get_address_display
from frappe.contacts.doctype.contact.contact import get_contact_name
from frappe.utils import cint, cstr, flt, get_fullname
import frappe.utils
from frappe.utils.nestedset import get_root_of
from datetime import datetime, timedelta

from erpnext.accounts.utils import get_account_name
from webshop.webshop.doctype.webshop_settings.webshop_settings import (
    get_shopping_cart_settings,
)
from webshop.webshop.utils.product import get_web_item_qty_in_stock
from erpnext.selling.doctype.quotation.quotation import _make_sales_order

frappe.utils.logger.set_log_level("DEBUG")
logger = frappe.logger("cart_api", allow_site=True, file_count=50)


class WebsitePriceListMissingError(frappe.ValidationError):
    pass


def set_cart_count(quotation=None):
    if cint(frappe.db.get_singles_value("Webshop Settings", "enabled")):
        if not quotation:
            quotation = _get_cart_quotation()
        cart_count = cstr(cint(quotation.get("total_qty")))

        if hasattr(frappe.local, "cookie_manager"):
            frappe.local.cookie_manager.set_cookie("cart_count", cart_count)


@frappe.whitelist(allow_guest=True)
def get_cart_quotation(doc=None, session_id=None):
    try:
        # Request headers
        headers = dict(frappe.request.headers)
        # Request body
        body = frappe.request.get_data(as_text=True)
        # Query parameters
        query_params = dict(frappe.request.args)
        # Cookies
        cookies = dict(frappe.request.cookies)
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Check if the user is logged in
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            if session_id is None:
                frappe.throw("Should include session id for Guest user.")
        if session_id:
            frappe.set_user("Guest")

        # Get the party (customer)
        party = get_party()

        # Fetch or create the quotation (cart)
        if not doc:
            if frappe.local.session.user is None or frappe.session.user == "Guest":
                quotation = frappe.get_all(
                    "Quotation",
                    filters={"custom_session_id": session_id, "docstatus": 0},
                    limit=1,
                )
                if quotation:
                    quotation = frappe.get_doc("Quotation", quotation[0].name)
                else:
                    frappe.throw("Cart is emplty!!")
            else:
                quotation = _get_cart_quotation(party)

            doc = quotation
            set_cart_count(quotation)

        # Get addresses for the party
        addresses = get_address_docs(party=party)
        coupon_code = None

        if quotation.coupon_code:
            coupon = frappe.get_doc("Coupon Code", {"name": quotation.coupon_code})
            # Access details from the coupon document
            coupon_code = coupon.coupon_code

        # Update billing address if none exists and addresses are available
        if not doc.customer_address and addresses:
            update_cart_address("billing", addresses[0].name)

        f_billing_address = get_formatted_address(quotation.customer_address)
        f_shipping_address = get_formatted_address(quotation.shipping_address_name)
        is_ready_for_order = False
        if quotation.custom_delivery_method == "Home Delivery":
            if (
                f_billing_address
                and f_shipping_address
                and quotation.custom_delivery_slot
                and quotation.contact_display
                and quotation.contact_mobile
            ):
                is_ready_for_order = True
        elif quotation.custom_delivery_method == "Store Pickup":
            if quotation.custom_pickup_store and quotation.custom_store_pickup_datetime:
                is_ready_for_order = True

        cart = {
            "session_id": quotation.custom_session_id,
            "contact_name": quotation.contact_display,
            "contact_mobile": quotation.contact_mobile,
            "contact_email": quotation.contact_email,
            "net_total": quotation.net_total,
            "taxes_and_charges": quotation.base_total_taxes_and_charges,
            "grand_total": quotation.grand_total,
            "rounding_adjustment": quotation.grand_total,
            "rounding_adjustment": quotation.rounding_adjustment,
            "rounded_total": quotation.rounded_total,
            "in_words": quotation.in_words,
            "coupon_code": coupon_code,
            "is_coupon_applied": bool(quotation.coupon_code),
            "is_ready_for_order": is_ready_for_order,
            "delivery_option": {
                "delivery_method": quotation.custom_delivery_method,
                "delivery_type": quotation.custom_delivery_type,
                "delivery_slot": quotation.custom_delivery_slot,
                "store": quotation.custom_pickup_store,
                "store_pickup_time": quotation.custom_store_pickup_datetime,
            },
            "items": [
                {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "quantity": item.qty,
                    "base_price": item.price_list_rate,
                    "price": item.rate,
                    "discount_amount": item.discount_amount,
                    "amount": item.amount,
                    "image": item.image,
                }
                for item in quotation.items
            ],
            "taxes": [
                {
                    "tax_type": tax.description,
                    "tax_rate": tax.rate,
                    "tax_amount": tax.tax_amount,
                }
                for tax in quotation.taxes
            ],
            # "shipping_address": get_shipping_addresses(party)[0],
            "billing_address": f_billing_address,
            "shipping_address": f_shipping_address,
        }

        # # Return the cart quotation and related data
        # return {
        #     "doc": decorate_quotation_doc(doc),
        #     "shipping_addresses": get_shipping_addresses(party),
        #     "billing_addresses": get_billing_addresses(party),
        #     "shipping_rules": get_applicable_shipping_rules(party),
        #     # "cart_settings": frappe.get_cached_doc("Webshop Settings"),
        # }
        frappe.response["data"] = {"status": "success", "cart": cart}

    except frappe.ValidationError as e:
        # Handle specific validation errors
        frappe.response["data"] = {
            "message": "There was a validation error",
            "error": str(e),
        }

    except frappe.DoesNotExistError:
        # Handle case where a document doesn't exist
        frappe.response["data"] = {"message": "Requested document does not exist"}

    except Exception as e:
        # General exception handling
        frappe.log_error(frappe.get_traceback(), _("Error in get_cart_quotation"))
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


def get_formatted_address(address):
    if address:
        address_doc = frappe.get_doc("Address", address)
        return {
            "address_line1": address_doc.address_line1,
            "address_line2": address_doc.address_line2,
            "city": address_doc.city,
            "state": address_doc.state,
            "pincode": address_doc.pincode,
            "country": address_doc.country,
        }
    else:
        return None


@frappe.whitelist()
def get_shipping_addresses(party=None):
    if not party:
        party = get_party()
    addresses = get_address_docs(party=party)
    return [
        {
            "address_line1": address.address_line1,
            "address_line2": address.address_line2,
            "city": address.city,
            "state": address.state,
            "pincode": address.pincode,
            "country": address.country,
        }
        for address in addresses
        if address.address_type == "Shipping"
    ]


@frappe.whitelist()
def get_billing_addresses(party=None):
    if not party:
        party = get_party()
    addresses = get_address_docs(party=party)
    return [
        {
            "address_line1": address.address_line1,
            "address_line2": address.address_line2,
            "city": address.city,
            "state": address.state,
            "pincode": address.pincode,
            "country": address.country,
        }
        for address in addresses
        if address.address_type == "Billing"
    ]


@frappe.whitelist(True)
def place_order_old(payment_method, session_id=None):
    try:
        # Check if Authorization header is present
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)

        # Validate authorization via API keys
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw(
                "Authorization header is malformed or missing API keys.",
                frappe.AuthenticationError,
            )

        validate_auth_via_api_keys(api_keys)

        # Check if the user is logged in
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            if session_id is None:
                frappe.throw("Guest user must provide session ID.", frappe.DataError)

        # Define the allowed payment methods
        allowed_payment_methods = ["card", "google_pay", "apple_pay", "paypal"]

        # Check if the payment method is valid
        if payment_method not in allowed_payment_methods:
            # Throw an exception if the payment method is invalid
            frappe.throw(
                f"Invalid payment method: {payment_method}. Allowed methods are: {', '.join(allowed_payment_methods)}.",
                frappe.ValidationError,
            )

        # Get the party (customer)
        party = get_party()

        # Fetch or create the quotation (cart)
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            quotation = frappe.get_all(
                "Quotation",
                filters={"custom_session_id": session_id, "docstatus": 0},
                limit=1,
            )
            if quotation:
                quotation = frappe.get_doc("Quotation", quotation[0].name)
            else:
                frappe.throw("Cart is empty!", frappe.ValidationError)
        else:
            quotation = _get_cart_quotation(party)

        # Check if there are no items in the quotation
        if not quotation.items or len(quotation.items) == 0:
            frappe.throw("Cart is empty!", frappe.ValidationError)

        # quotation = _get_cart_quotation()
        cart_settings = frappe.get_cached_doc("Webshop Settings")
        quotation.company = cart_settings.company

        if not (
            quotation.contact_display
            or quotation.contact_mobile
            or quotation.shipping_address_name
            or quotation.custom_delivery_method
            or quotation.customer_address
            or quotation.custom_delivery_slot
        ):
            frappe.throw("Cart is not ready to place order", frappe.ValidationError)

        delivery_slot = frappe.get_doc("Delivery Slot", quotation.custom_delivery_slot)

        quotation.flags.ignore_permissions = True
        quotation.submit()

        if quotation.quotation_to == "Lead" and quotation.party_name:
            # company used to create customer accounts
            frappe.defaults.set_user_default("company", quotation.company)

        customer_group = cart_settings.default_customer_group

        sales_order = frappe.get_doc(
            _make_sales_order(
                quotation.name, customer_group, ignore_permissions=True
            )
        )
        sales_order.payment_schedule = []

        if not cint(cart_settings.allow_items_not_in_stock):
            for item in sales_order.get("items"):
                item.warehouse = frappe.db.get_value(
                    "Website Item", {"item_code": item.item_code}, "website_warehouse"
                )
                is_stock_item = frappe.db.get_value(
                    "Item", item.item_code, "is_stock_item"
                )

                if is_stock_item:
                    item_stock = get_web_item_qty_in_stock(
                        item.item_code, "website_warehouse"
                    )
                    if not cint(item_stock.in_stock):
                        throw(_("{0} Not in Stock").format(item.item_code))
                    if item.qty > item_stock.stock_qty:
                        throw(
                            _("Only {0} in Stock for item {1}").format(
                                item_stock.stock_qty, item.item_code
                            )
                        )
        # Adding Delivery Method, Delivery Date And Delivery Slots data
        sales_order.custom_delivery_method = quotation.custom_delivery_method
        if quotation.custom_delivery_slot:
            sales_order.delivery_date, sales_order.custom_delivery_slot = (
                get_date_and_time_slot(delivery_slot)
            )

        sales_order.custom_payment_method = payment_method

        # Create a Stripe PaymentIntent
        stripe_keys = get_stripe_keys()
        ip_address = frappe.request.headers.get(
            "X-Forwarded-For"
        ) or frappe.request.headers.get("Remote-Addr")
        amount_in_cents = int(float(sales_order.rounded_total) * 100)
        shipping_address_doc = frappe.get_doc(
            "Address", quotation.shipping_address_name
        )
        intent = stripe.PaymentIntent.create(
            amount=amount_in_cents,
            currency=sales_order.currency,
            metadata={
                "integration_check": "accept_a_payment",
                "sales_order": sales_order.name,
                "contact_name": quotation.contact_display,
                "contact_email": quotation.contact_email,
                "contact_mobile": quotation.contact_mobile,
                "ip_address": ip_address,  # Include IP address in metadata
            },
            automatic_payment_methods={
                "enabled": True,
            },
            # payment_method_types=["card"],
            description=quotation.name + " cart checkout",
            receipt_email=quotation.contact_email,  # Include customer email
            shipping={
                "name": quotation.contact_display,  # Include customer name in shipping
                "address": {
                    "line1": shipping_address_doc.address_line1,
                    "line2": shipping_address_doc.address_line2,
                    "city": shipping_address_doc.city,
                    "state": shipping_address_doc.state,
                    "postal_code": shipping_address_doc.pincode,
                    "country": shipping_address_doc.country,
                },
            },
        )

        sales_order.custom_payment_reference = intent["id"]
        sales_order.flags.ignore_permissions = True
        sales_order.save()
        sales_order.submit()
        frappe.db.set_value("Sales Order", sales_order.name, "status", "To Bill")
        frappe.db.commit()

        if hasattr(frappe.local, "cookie_manager"):
            frappe.local.cookie_manager.delete_cookie("cart_count")

        frappe.local.response["http_status_code"] = HTTPStatus.OK
        frappe.response["data"] = {
            "status": "success",
            "sales_order": sales_order.name,
            "intent_id": intent["id"],
            "client_secret": intent["client_secret"],
        }

    except Exception as e:
        # Rollback the transaction in case of any error
        frappe.db.rollback()
        # Log the error for debugging
        frappe.log_error(frappe.get_traceback(), "Place Order Failed")
        # Raise the error to inform the client
        frappe.throw(_("There was an error processing the order: {0}").format(str(e)))


def cancel_stripe_payment_intent(sales_order):
    # Retrieve and cancel the Stripe PaymentIntent linked to this Sales Order
    stripe_payment_id = sales_order.custom_payment_reference
    if stripe_payment_id:
        try:
            # Create a Stripe PaymentIntent
            stripe_keys = get_stripe_keys()
            stripe.PaymentIntent.cancel(stripe_payment_id)
            frappe.msgprint(
                f"Stripe Payment Intent {stripe_payment_id} has been cancelled."
            )
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Stripe Payment Cancel Failed")
            frappe.throw(f"Failed to cancel Stripe Payment Intent: {str(e)}")


def get_date_and_time_slot(delivery_slot):
    # Extract information from delivery_slot
    day_name = delivery_slot.day
    start_time = delivery_slot.start_time
    end_time = delivery_slot.end_time

    # Create a list of day names in order, starting from Monday (0) to Sunday (6)
    days_of_week = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]

    # Get the current date and today's weekday as an integer (Monday is 0, Sunday is 6)
    today = datetime.now()
    today_weekday = today.weekday()

    # Find the index of the desired day in the week
    try:
        target_weekday = days_of_week.index(day_name)
    except ValueError:
        raise ValueError(f"Invalid day name: {day_name}")

    # Calculate the number of days between today and the target day
    days_until_target = (target_weekday - today_weekday) % 7

    # Calculate the date of the target day
    target_date = today + timedelta(days=days_until_target)
    target_date_str = target_date.strftime("%Y-%m-%d")

    # Combine the date with the start and end time
    time_slot = f"{start_time} - {end_time}"

    # Return the formatted string with date and time slot
    return target_date_str, time_slot


@frappe.whitelist(True)
def cancel_order(sales_order, session_id=None):
    try:
        # Check if Authorization header is present
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)

        # Validate authorization via API keys
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw(
                "Authorization header is malformed or missing API keys.",
                frappe.AuthenticationError,
            )

        validate_auth_via_api_keys(api_keys)

        # Check if the user is logged in
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            if session_id is None:
                frappe.throw("Guest user must provide session ID.", frappe.DataError)

        # Fetch the sales order document
        sales_order = frappe.get_doc("Sales Order", sales_order)

        if sales_order.docstatus != 1:  # Check if the Sales Order is submitted
            frappe.throw("Sales Order is not submitted or already cancelled.")

        # Check if the Sales Order is linked to a Quotation via Sales Order Items
        quotation_name = sales_order.items[0].prevdoc_docname
        # for item in sales_order.items:
        #     if item.get("prevdoc_doctype") == "Quotation" and item.get("prevdoc_docname"):
        #         quotation_name = item.get("prevdoc_docname")
        #         break

        # Cancel the Sales Order
        # Bypass permission checks for cancellation
        sales_order.flags.ignore_permissions = True
        sales_order.cancel()

        if quotation_name:
            # Fetch and cancel the related Quotation
            quotation = frappe.get_doc("Quotation", quotation_name)
            if quotation.docstatus == 1:
                quotation.flags.ignore_permissions = True
                quotation.cancel()
                frappe.msgprint(
                    f"Related Quotation {quotation_name} has been cancelled."
                )

            # Create a new Quotation as a draft
            new_quotation = frappe.copy_doc(quotation)
            new_quotation.docstatus = 0  # Set as draft
            apply_cart_settings(quotation=new_quotation)
            new_quotation.save(ignore_permissions=True)
            frappe.msgprint(
                f"A new draft Quotation {new_quotation.name} has been created from the cancelled Quotation."
            )

        # Handle stock reversal (optional, if you had any stock reserved)
        # You might want to restore reserved stock if applicable

        # Clear any related session data or cart count cookies
        if hasattr(frappe.local, "cookie_manager"):
            frappe.local.cookie_manager.delete_cookie("cart_count")

        # If payment is done through Stripe, cancel the Payment Intent
        if sales_order.custom_payment_method == "card":
            cancel_stripe_payment_intent(sales_order)

        frappe.local.response["http_status_code"] = HTTPStatus.OK
        frappe.response["data"] = {
            "status": "success",
            "message": "Order have been cancelled.",
        }

    except Exception as e:
        frappe.throw(_("Error during cancellation: {0}").format(str(e)))


@frappe.whitelist()
def request_for_quotation():
    quotation = _get_cart_quotation()
    quotation.flags.ignore_permissions = True

    if get_shopping_cart_settings().save_quotations_as_draft:
        quotation.save()
    else:
        quotation.submit()

    return quotation.name


@frappe.whitelist(True)
def update_cart(item_code, qty, additional_notes=None):
    try:
        # Validate authorization via API keys
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )
        usr = frappe.local.session.user
        quotation = _get_cart_quotation()

        empty_cart = False
        qty = flt(qty)

        if qty == 0:
            # Remove item from cart if quantity is 0
            quotation_items = quotation.get("items", {"item_code": ["!=", item_code]})
            if quotation_items:
                quotation.set("items", quotation_items)
            else:
                empty_cart = True
        else:
            # Fetch minimum and maximum quantity limits
            item_name, stock_uom, min_qty, max_qty = frappe.db.get_value(
                "Item", item_code, ["item_name", "stock_uom", "custom_minimum_cart_qty", "custom_maximum_cart_qty"]
            )

            # Default to 0 if the values are None
            min_qty = min_qty or 0
            max_qty = max_qty or 0

            # Validate the requested quantity
            if min_qty and qty < min_qty:
                frappe.throw(
                    _("Minimum order quantity for {0} is {1} {2}.").format(
                        item_name, min_qty, stock_uom
                    )
                )

            if max_qty and qty > max_qty:
                frappe.throw(
                    _("Maximum order quantity for {0} is {1} {2}.").format(
                        item_name, max_qty, stock_uom
                    )
                )
            # Fetch warehouse and stock information
            warehouse = frappe.get_cached_value(
                "Website Item", {"item_code": item_code}, "website_warehouse"
            )

            # Verify projected qty (available_qty - reserved_qty)
            projected_qty = frappe.get_cached_value(
                "Bin", {"item_code": item_code, "warehouse": warehouse}, "projected_qty"
            )

            # Check if sufficient stock is available
            if projected_qty < qty:
                frappe.throw(
                    _("Only {0} units of {1} are available in stock.").format(
                        projected_qty, item_code
                    )
                )

            # Update or add item to quotation
            quotation_items = quotation.get("items", {"item_code": item_code})
            if not quotation_items:
                quotation.append(
                    "items",
                    {
                        "doctype": "Quotation Item",
                        "item_code": item_code,
                        "qty": qty,
                        "additional_notes": additional_notes,
                        "warehouse": warehouse,
                    },
                )
            else:
                quotation_items[0].qty = qty
                quotation_items[0].warehouse = warehouse
                quotation_items[0].additional_notes = additional_notes

        # Apply cart settings and save or delete the quotation
        apply_cart_settings(quotation=quotation)
        quotation.flags.ignore_permissions = True
        quotation.payment_schedule = []
        if not empty_cart:
            quotation.save()
        else:
            quotation.delete()
            quotation = None

        set_cart_count(quotation)

        # Set response on success
        frappe.local.response["http_status_code"] = HTTPStatus.OK
        frappe.response["data"] = {"message": "Successfully updated the user's cart"}

    except frappe.DoesNotExistError as e:
        # Handle missing records
        frappe.log_error(f"Record not found: {e}", "Cart Update Error")
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        frappe.response["data"] = {"error": str(e)}

    except frappe.ValidationError as e:
        # Handle validation issues
        frappe.log_error(f"Validation error: {e}", "Cart Update Error")
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {"error": str(e)}

    except Exception as e:
        # Handle unexpected errors
        frappe.log_error(f"Unexpected error: {e}", "Cart Update Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {"error": "An unexpected error occurred"}


@frappe.whitelist()
def get_shopping_cart_menu(context=None):
    if not context:
        context = get_cart_quotation()

    return frappe.render_template("templates/includes/cart/cart_dropdown.html", context)


@frappe.whitelist()
def add_new_address(doc):
    doc = frappe.parse_json(doc)
    doc.update({"doctype": "Address"})
    address = frappe.get_doc(doc)
    address.save(ignore_permissions=True)

    return address


@frappe.whitelist(allow_guest=True)
def create_lead_for_item_inquiry(lead, subject, message):
    lead = frappe.parse_json(lead)
    lead_doc = frappe.new_doc("Lead")
    for fieldname in ("lead_name", "company_name", "email_id", "phone"):
        lead_doc.set(fieldname, lead.get(fieldname))

    lead_doc.set("lead_owner", "")

    if not frappe.db.exists("Lead Source", "Product Inquiry"):
        frappe.get_doc(
            {"doctype": "Lead Source", "source_name": "Product Inquiry"}
        ).insert(ignore_permissions=True)

    lead_doc.set("source", "Product Inquiry")

    try:
        lead_doc.save(ignore_permissions=True)
    except frappe.exceptions.DuplicateEntryError:
        frappe.clear_messages()
        lead_doc = frappe.get_doc("Lead", {"email_id": lead["email_id"]})

    lead_doc.add_comment(
        "Comment",
        text="""
		<div>
			<h5>{subject}</h5>
			<p>{message}</p>
		</div>
	""".format(
            subject=subject, message=message
        ),
    )

    return lead_doc


@frappe.whitelist()
def get_terms_and_conditions(terms_name):
    return frappe.db.get_value("Terms and Conditions", terms_name, "terms")


@frappe.whitelist()
def update_cart_address(address_type, address_name):
    quotation = _get_cart_quotation()
    address_doc = frappe.get_doc("Address", address_name).as_dict()
    address_display = get_address_display(address_doc)

    if address_type.lower() == "billing":
        quotation.customer_address = address_name
        quotation.address_display = address_display
        quotation.shipping_address_name = (
            quotation.shipping_address_name or address_name
        )
        address_doc = next(
            (doc for doc in get_billing_addresses() if doc["name"] == address_name),
            None,
        )
    elif address_type.lower() == "shipping":
        quotation.shipping_address_name = address_name
        quotation.shipping_address = address_display
        quotation.customer_address = quotation.customer_address or address_name
        address_doc = next(
            (doc for doc in get_shipping_addresses() if doc["name"] == address_name),
            None,
        )
    apply_cart_settings(quotation=quotation)

    quotation.flags.ignore_permissions = True
    quotation.save()

    context = get_cart_quotation(quotation)
    context["address"] = address_doc

    return {
        "taxes": frappe.render_template(
            "templates/includes/order/order_taxes.html", context
        ),
        "address": frappe.render_template(
            "templates/includes/cart/address_card.html", context
        ),
    }


def guess_territory():
    territory = None
    geoip_country = frappe.session.get("session_country")
    if geoip_country:
        territory = frappe.db.get_value("Territory", geoip_country)

    return territory or get_root_of("Territory")


def decorate_quotation_doc(doc):
    for d in doc.get("items", []):
        item_code = d.item_code
        fields = ["web_item_name", "thumbnail", "website_image", "description", "route"]

        # Variant Item
        if not frappe.db.exists("Website Item", {"item_code": item_code}):
            variant_data = frappe.db.get_values(
                "Item",
                filters={"item_code": item_code},
                fieldname=["variant_of", "item_name", "image"],
                as_dict=True,
            )[0]
            item_code = variant_data.variant_of
            fields = fields[1:]
            d.web_item_name = variant_data.item_name

            if variant_data.image:  # get image from variant or template web item
                d.thumbnail = variant_data.image
                fields = fields[2:]

        d.update(
            frappe.db.get_value(
                "Website Item", {"item_code": item_code}, fields, as_dict=True
            )
        )

        website_warehouse = frappe.get_cached_value(
            "Website Item", {"item_code": item_code}, "website_warehouse"
        )

        d.warehouse = website_warehouse

    return doc


def _get_cart_quotation(party=None):
    """Return the open Quotation of type "Shopping Cart" or make a new one"""
    if not party:
        party = get_party()

    quotation = frappe.get_all(
        "Quotation",
        fields=["name"],
        filters={
            "party_name": party.name,
            "contact_email": frappe.session.user,
            "order_type": "Shopping Cart",
            "docstatus": 0,
            # "status": ["in", ["Draft", "Open"]]
            # "valid_upto": ["in", ["", None, ["gt", frappe.utils.nowdate()]]],
        },
        order_by="modified desc",
        limit_page_length=1,
    )

    if quotation:
        qdoc = frappe.get_doc("Quotation", quotation[0].name)
    else:
        company = frappe.db.get_single_value("Webshop Settings", "company")
        qdoc = frappe.get_doc(
            {
                "doctype": "Quotation",
                "naming_series": get_shopping_cart_settings().quotation_series
                or "QTN-CART-",
                "quotation_to": party.doctype,
                "company": company,
                "order_type": "Shopping Cart",
                "status": "Draft",
                "docstatus": 0,
                "__islocal": 1,
                "party_name": party.name,
            }
        )

        qdoc.contact_person = frappe.db.get_value(
            "Contact", {"email_id": frappe.session.user}
        )
        qdoc.contact_email = frappe.session.user
        qdoc.custom_delivery_method = 'Home Delivery'

        qdoc.flags.ignore_permissions = True
        qdoc.run_method("set_missing_values")
        apply_cart_settings(party, qdoc)

    return qdoc


@frappe.whitelist(True)
def update_party(fullname, company_name=None, mobile_no=None, phone=None, email=None):
    party = get_party()

    party.customer_name = company_name or fullname
    party.customer_type = "Company" if company_name else "Individual"

    contact_name = frappe.db.get_value("Contact", {"email_id": frappe.session.user})
    contact = frappe.get_doc("Contact", contact_name)
    contact.first_name = fullname
    contact.last_name = None
    contact.customer_name = party.customer_name
    contact.mobile_no = mobile_no
    contact.phone = phone
    contact.flags.ignore_permissions = True
    contact.save()

    party_doc = frappe.get_doc(party.as_dict())
    party_doc.flags.ignore_permissions = True
    party_doc.save()

    qdoc = _get_cart_quotation(party)
    if not qdoc.get("__islocal"):
        qdoc.customer_name = company_name or fullname
        # qdoc.run_method("set_missing_lead_customer_details")
        qdoc.flags.ignore_permissions = True
        qdoc.save()


def apply_cart_settings(party=None, quotation=None):
    if not party:
        party = get_party()
    if not quotation:
        quotation = _get_cart_quotation(party)

    cart_settings = frappe.get_cached_doc("Webshop Settings")

    set_price_list_and_rate(quotation, cart_settings)

    quotation.run_method("calculate_taxes_and_totals")

    set_taxes(quotation, cart_settings)

    if quotation.custom_delivery_method and len(quotation.items) > 0:
        _apply_shipping_rule(party, quotation, cart_settings)


def set_price_list_and_rate(quotation, cart_settings):
    """set price list based on billing territory"""

    _set_price_list(cart_settings, quotation)

    # reset values
    quotation.price_list_currency = quotation.currency = (
        quotation.plc_conversion_rate
    ) = quotation.conversion_rate = None
    for item in quotation.get("items"):
        item.price_list_rate = item.discount_percentage = item.rate = item.amount = None

    # refetch values
    quotation.run_method("set_price_list_and_item_details")

    if hasattr(frappe.local, "cookie_manager"):
        # set it in cookies for using in product page
        frappe.local.cookie_manager.set_cookie(
            "selling_price_list", quotation.selling_price_list
        )


def _set_price_list(cart_settings, quotation=None):
    """Set price list based on customer or shopping cart default"""
    from erpnext.accounts.party import get_default_price_list

    party_name = quotation.get("party_name") if quotation else get_party().get("name")
    selling_price_list = None

    # check if default customer price list exists
    if party_name and frappe.db.exists("Customer", party_name):
        selling_price_list = get_default_price_list(
            frappe.get_doc("Customer", party_name)
        )

    # check default price list in shopping cart
    if not selling_price_list:
        selling_price_list = cart_settings.price_list

    if quotation:
        quotation.selling_price_list = selling_price_list

    return selling_price_list


def set_taxes(quotation, cart_settings):
    """set taxes based on billing territory"""
    from erpnext.accounts.party import set_taxes

    customer_group = frappe.db.get_value(
        "Customer", quotation.party_name, "customer_group"
    )

    quotation.taxes_and_charges = set_taxes(
        quotation.party_name,
        "Customer",
        quotation.transaction_date,
        quotation.company,
        customer_group=customer_group,
        supplier_group=None,
        tax_category=quotation.tax_category,
        billing_address=quotation.customer_address,
        shipping_address=quotation.shipping_address_name,
        use_for_shopping_cart=1,
    )
    #
    # 	# clear table
    quotation.set("taxes", [])
    #
    # 	# append taxes
    quotation.append_taxes_from_master()
    quotation.append_taxes_from_item_tax_template()


def get_party(user=None):
    if not user:
        user = frappe.session.user

    contact_name = get_contact_name(user)
    party = None

    if contact_name:
        contact = frappe.get_doc("Contact", contact_name)
        if contact.links:
            party_doctype = contact.links[0].link_doctype
            party = contact.links[0].link_name

    cart_settings = frappe.get_cached_doc("Webshop Settings")

    debtors_account = ""

    if cart_settings.enable_checkout:
        debtors_account = get_debtors_account(cart_settings)

    if party:
        doc = frappe.get_doc(party_doctype, party)
        if doc.doctype in ["Customer", "Supplier"]:
            if not frappe.db.exists("Portal User", {"parent": doc.name, "user": user}):
                doc.append("portal_users", {"user": user})
                doc.flags.ignore_permissions = True
                doc.flags.ignore_mandatory = True
                doc.save()

        return doc

    else:
        if not cart_settings.enabled:
            frappe.local.flags.redirect_location = "/contact"
            raise frappe.Redirect
        customer = frappe.new_doc("Customer")
        fullname = get_fullname(user)
        customer.update(
            {
                "customer_name": fullname,
                "customer_type": "Individual",
                "customer_group": get_shopping_cart_settings().default_customer_group,
                "territory": get_root_of("Territory"),
            }
        )

        customer.append("portal_users", {"user": user})

        if debtors_account:
            customer.update(
                {
                    "accounts": [
                        {"company": cart_settings.company, "account": debtors_account}
                    ]
                }
            )

        customer.flags.ignore_mandatory = True
        customer.insert(ignore_permissions=True)

        contact = frappe.new_doc("Contact")
        contact.update(
            {"first_name": fullname, "email_ids": [{"email_id": user, "is_primary": 1}]}
        )
        contact.append("links", dict(link_doctype="Customer", link_name=customer.name))
        contact.flags.ignore_mandatory = True
        contact.insert(ignore_permissions=True)

        return customer


def get_debtors_account(cart_settings):
    if not cart_settings.payment_gateway_account:
        frappe.throw(_("Payment Gateway Account not set"), _("Mandatory"))

    payment_gateway_account_currency = frappe.get_doc(
        "Payment Gateway Account", cart_settings.payment_gateway_account
    ).currency

    account_name = _("Debtors ({0})").format(payment_gateway_account_currency)

    debtors_account_name = get_account_name(
        "Receivable",
        "Asset",
        is_group=0,
        account_currency=payment_gateway_account_currency,
        company=cart_settings.company,
    )

    if not debtors_account_name:
        debtors_account = frappe.get_doc(
            {
                "doctype": "Account",
                "account_type": "Receivable",
                "root_type": "Asset",
                "is_group": 0,
                "parent_account": get_account_name(
                    root_type="Asset", is_group=1, company=cart_settings.company
                ),
                "account_name": account_name,
                "currency": payment_gateway_account_currency,
            }
        ).insert(ignore_permissions=True)

        return debtors_account.name

    else:
        return debtors_account_name


def get_address_docs(
    doctype=None,
    txt=None,
    filters=None,
    limit_start=0,
    limit_page_length=20,
    party=None,
):
    if not party:
        party = get_party()

    if not party:
        return []

    address_names = frappe.db.get_all(
        "Dynamic Link",
        fields=("parent"),
        filters=dict(
            parenttype="Address", link_doctype=party.doctype, link_name=party.name
        ),
    )

    out = []

    for a in address_names:
        address = frappe.get_doc("Address", a.parent)
        address.display = get_address_display(address.as_dict())
        out.append(address)

    return out


@frappe.whitelist()
def apply_shipping_rule(shipping_rule):
    quotation = _get_cart_quotation()

    quotation.shipping_rule = shipping_rule

    apply_cart_settings(quotation=quotation)

    quotation.flags.ignore_permissions = True
    quotation.save()

    return get_cart_quotation(quotation)


def _apply_shipping_rule(party=None, quotation=None, cart_settings=None):
    if not quotation:
        frappe.throw(_("Quotation document is required for applying shipping rules."))

    # Initialize shipping_rules as None
    shipping_rules = None

    # Check if shipping rule is not already set
    # if not quotation.shipping_rule:
    # Check if custom delivery method is set
    if quotation.custom_delivery_method:
        # Handle store pickup scenario
        if quotation.custom_delivery_method == "Store Pickup":
            try:
                shipping_rules = [frappe.get_doc("Shipping Rule", "Store Pickup").name]

            except frappe.DoesNotExistError:
                frappe.throw(_("Store Pickup shipping rule does not exist."))
        elif quotation.custom_delivery_method == "Home Delivery":
            try:
                if quotation.custom_delivery_type == "Express Delivery":
                    shipping_rules = [
                        frappe.get_doc("Shipping Rule", "Express Delivery").name
                    ]
                else:
                    shipping_rules = [
                        frappe.get_doc("Shipping Rule", "Standard Delivery").name
                    ]

            except frappe.DoesNotExistError:
                frappe.throw(_("Store Pickup shipping rule does not exist."))
    else:
        # Get available shipping rules based on the quotation and cart settings
        shipping_rules = get_shipping_rules(quotation, cart_settings)

    # if not shipping_rules:
    #     shipping_rules = get_shipping_rules(quotation, cart_settings)
    #     quotation.shipping_rule = shipping_rules[0]

    # # Set the first shipping rule from the list if not already applied
    # elif quotation.shipping_rule not in shipping_rules:
    #     quotation.shipping_rule = shipping_rules[0]
    # shipping_rules = get_shipping_rules(quotation, cart_settings)
    if shipping_rules:
        quotation.shipping_rule = shipping_rules[0]

    # Validate and apply the shipping rule
    if quotation.shipping_rule:
        try:
            quotation.run_method("apply_shipping_rule")
            quotation.run_method("calculate_taxes_and_totals")
        except Exception as e:
            frappe.log_error(
                f"Failed to apply shipping rule: {str(e)}",
                "Shipping Rule Application Error",
            )
            frappe.throw(
                _(
                    "Failed to apply shipping rule. Please check the logs for more details."
                )
            )


def get_applicable_shipping_rules(party=None, quotation=None):
    shipping_rules = get_shipping_rules(quotation)

    if shipping_rules:
        rule_label_map = frappe.db.get_values("Shipping Rule", shipping_rules, "label")
        # we need this in sorted order as per the position of the rule in the settings page
        return [rule for rule in shipping_rules]


def get_shipping_rules(quotation=None, cart_settings=None):
    if not quotation:
        quotation = _get_cart_quotation()

    shipping_rules = []
    if quotation.shipping_address_name:
        country = frappe.db.get_value(
            "Address", quotation.shipping_address_name, "country"
        )
        if country:
            sr_country = frappe.qb.DocType("Shipping Rule Country")
            sr = frappe.qb.DocType("Shipping Rule")
            query = (
                frappe.qb.from_(sr_country)
                .join(sr)
                .on(sr.name == sr_country.parent)
                .select(sr.name)
                .distinct()
                .where((sr_country.country == country) & (sr.disabled != 1))
            )
            result = query.run(as_list=True)
            shipping_rules = [x[0] for x in result]

    return shipping_rules


def get_address_territory(address_name):
    """Tries to match city, state and country of address to existing territory"""
    territory = None

    if address_name:
        address_fields = frappe.db.get_value(
            "Address", address_name, ["city", "state", "country"]
        )
        for value in address_fields:
            territory = frappe.db.get_value("Territory", value)
            if territory:
                break

    return territory


def show_terms(doc):
    return doc.tc_name


@frappe.whitelist(allow_guest=True)
def apply_coupon_code(
    applied_code, session_id=None, applied_referral_sales_partner=None
):
    try:
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Check if the user is logged in
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            if session_id is None:
                frappe.throw("Should include session id for Guest user.")

        # Ensure a coupon code is provided
        if not applied_code:
            frappe.throw(_("Please enter a coupon code"))

        # Check if the coupon exists
        coupon_list = frappe.get_all(
            "Coupon Code", filters={"coupon_code": applied_code}
        )
        if not coupon_list:
            frappe.throw(_("Please enter a valid coupon code"))

        coupon_name = coupon_list[0].name

        # Validate the coupon code
        from erpnext.accounts.doctype.pricing_rule.utils import validate_coupon_code

        validate_coupon_code(coupon_name)

        if frappe.local.session.user is None or frappe.session.user == "Guest":
            quotation = frappe.get_all(
                "Quotation",
                filters={"custom_session_id": session_id, "docstatus": 0},
                limit=1,
            )
            if quotation:
                # Fetch the existing quotation for the session
                quotation = frappe.get_doc("Quotation", quotation[0].name)
        else:
            quotation = _get_cart_quotation()

        if not quotation.items:
            frappe.throw(_("Empty cart", frappe.ValidationError))
        quotation.coupon_code = coupon_name
        quotation.flags.ignore_permissions = True
        quotation.save()

        # Check if a referral sales partner code is provided
        if applied_referral_sales_partner:
            sales_partner_list = frappe.get_all(
                "Sales Partner",
                filters={"referral_code": applied_referral_sales_partner},
            )
            if sales_partner_list:
                sales_partner_name = sales_partner_list[0].name
                quotation.referral_sales_partner = sales_partner_name
                quotation.flags.ignore_permissions = True
                quotation.save()

        return quotation

    except frappe.ValidationError as e:
        # Handle specific validation errors
        frappe.response["data"] = {
            "message": "There was a validation error",
            "error": str(e),
        }

    except frappe.DoesNotExistError:
        frappe.response["data"] = {"message": "Requested document does not exist"}

    except Exception as e:
        # General exception handling
        frappe.log_error(frappe.get_traceback(), _("Error in apply_coupon_code"))
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True)
def update_guest_cart(
    session_id, item_code, qty, with_items=None, additional_notes=None
):
    try:
        default_customer = "Guest"
        empty_card = False

        quotation = frappe.get_all(
            "Quotation", filters={"custom_session_id": session_id, "docstatus": 0}, limit=1
        )

        if quotation:
            # Fetch the existing quotation for the session
            quotation = frappe.get_doc("Quotation", quotation[0].name)
            qty = flt(qty)
            if qty == 0:
                quotation_items = quotation.get("items", {"item_code": ["!=", item_code]})
                if quotation_items:
                    quotation.set("items", quotation_items)
                else:
                    empty_card = True

            else:
                # Fetch minimum and maximum quantity limits
                item_name, stock_uom, min_qty, max_qty = frappe.db.get_value(
                    "Item", item_code, ["item_name", "stock_uom", "custom_minimum_cart_qty", "custom_maximum_cart_qty"]
                )

                # Default to 0 if the values are None
                min_qty = min_qty or 0
                max_qty = max_qty or 0

                # Validate the requested quantity
                if min_qty and qty < min_qty:
                    frappe.throw(
                        _("Minimum order quantity for {0} is {1} {2}.").format(
                            item_name, min_qty, stock_uom
                        )
                    )

                if max_qty and qty > max_qty:
                    frappe.throw(
                        _("Maximum order quantity for {0} is {1}.").format(
                            item_name, max_qty
                        )
                    )
                warehouse = frappe.get_cached_value(
                    "Website Item", {"item_code": item_code}, "website_warehouse"
                )

                # Verify projected qty (available_qty - reserved_qty)
                projected_qty = frappe.get_cached_value(
                    "Bin", {"item_code": item_code, "warehouse": warehouse}, "projected_qty"
                )

                # Check if sufficient stock is available
                if projected_qty < qty:
                    frappe.throw(
                        _("Only {0} units of {1} are available in stock.").format(
                            projected_qty, item_code
                        )
                    )

                quotation_items = quotation.get("items", {"item_code": item_code})
                if not quotation_items:
                    quotation.append(
                        "items",
                        {
                            "doctype": "Quotation Item",
                            "item_code": item_code,
                            "qty": qty,
                            "additional_notes": additional_notes,
                            "warehouse": warehouse,
                        },
                    )
                else:
                    quotation_items[0].qty = qty
                    quotation_items[0].warehouse = warehouse
                    quotation_items[0].additional_notes = additional_notes
        else:
            # Create a new quotation for the session
            company = frappe.db.get_single_value("Webshop Settings", "company")
            quotation = frappe.get_doc(
                {
                    "doctype": "Quotation",
                    "quotation_to": "Customer",  # You can change this to "Lead" if needed
                    "party_name": default_customer,  # Associate with the default customer
                    "transaction_date": frappe.utils.nowdate(),
                    "custom_session_id": session_id,  # Custom field to track guest session
                    "items": [],
                    "company": company,
                    "order_type": "Shopping Cart",
                    "status": "Draft",
                    "docstatus": 0,
                }
            )

            # Add item to quotation
            warehouse = frappe.get_cached_value(
                "Website Item", {"item_code": item_code}, "website_warehouse"
            )

            # Append the item if not already added
            quotation.append(
                "items",
                {
                    "doctype": "Quotation Item",
                    "item_code": item_code,
                    "qty": flt(qty),
                    "warehouse": warehouse,
                },
            )

        apply_cart_settings(quotation=quotation)

        quotation.flags.ignore_permissions = True

        quotation_name = quotation.name

        if not empty_card:
            quotation.save()
        else:
            quotation.delete()
            quotation = None

        set_cart_count(quotation)

        if quotation:
            return quotation.name
        else:
            return quotation_name
    except frappe.DoesNotExistError as e:
        # Handle missing records
        frappe.log_error(f"Record not found: {e}", "Cart Update Error")
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        frappe.response["data"] = {"error": str(e)}

    except frappe.ValidationError as e:
        # Handle validation issues
        frappe.log_error(f"Validation error: {e}", "Cart Update Error")
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {"error": str(e)}

    except Exception as e:
        # Handle unexpected errors
        frappe.log_error(f"Unexpected error: {e}", "Cart Update Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {"error": "An unexpected error occurred"}


def get_stripe_keys():
    stripe_settings = frappe.get_doc("Stripe Settings", "Stripe")

    if not stripe_settings.secret_key:
        frappe.throw(
            _("Stripe Secret Key is missing. Please configure Stripe Settings.")
        )

    stripe.api_key = stripe_settings.get_password(
        fieldname="secret_key", raise_exception=False
    )
    return {
        "secret_key": stripe_settings.get_password(
            fieldname="secret_key", raise_exception=False
        ),
        "publishable_key": stripe_settings.publishable_key,
        "redirect_url": stripe_settings.redirect_url,
    }


@frappe.whitelist(allow_guest=True)
def place_order(payment_method, session_id=None):
    stripe_keys = get_stripe_keys()

    try:
        # Check if Authorization header is present
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)

        # Validate authorization via API keys
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw(
                "Authorization header is malformed or missing API keys.",
                frappe.AuthenticationError,
            )

        validate_auth_via_api_keys(api_keys)

        # Check if the user is logged in
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            if session_id is None:
                frappe.throw("Guest user must provide session ID.", frappe.DataError)
        
        if session_id:
            frappe.set_user("Guest")

        # Define the allowed payment methods
        allowed_payment_methods = ["card", "google_pay", "apple_pay", "paypal"]

        # Check if the payment method is valid
        if payment_method not in allowed_payment_methods:
            # Throw an exception if the payment method is invalid
            frappe.throw(
                f"Invalid payment method: {payment_method}. Allowed methods are: {', '.join(allowed_payment_methods)}.",
                frappe.ValidationError,
            )

        # Get the party (customer)
        party = get_party()

        # Fetch or create the quotation (cart)
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            quotation = frappe.get_all(
                "Quotation",
                filters={"custom_session_id": session_id, "docstatus": 0},
                limit=1,
            )
            if quotation:
                quotation = frappe.get_doc("Quotation", quotation[0].name)
            else:
                frappe.throw("Cart is empty!", frappe.ValidationError)
        else:
            quotation = _get_cart_quotation(party)

        # Check if there are no items in the quotation
        if not quotation.items or len(quotation.items) == 0:
            frappe.throw("Cart is empty!", frappe.ValidationError)

        # quotation = _get_cart_quotation()
        cart_settings = frappe.get_cached_doc("Webshop Settings")
        quotation.company = cart_settings.company

        if not (
            quotation.contact_display
            or quotation.contact_mobile
            or quotation.contact_email
            or quotation.shipping_address_name
            or quotation.custom_delivery_method
            or quotation.customer_address
            or quotation.custom_delivery_slot
        ):
            frappe.throw("Cart is not ready to place order", frappe.ValidationError)

        # Validate the amount with quotation's total amount
        quotation_total = float(quotation.rounded_total or quotation.grand_total)

        # Extract details from the quotation
        description = quotation.name
        customer_email = quotation.contact_email
        customer_name = quotation.contact_display

        # Initialize billing and shipping address
        billing_address = {}
        shipping_address = {}

        # Fetch billing address if available
        if quotation.customer_address:
            baddress = frappe.db.get_value(
                "Address",
                quotation.customer_address,
                [
                    "address_line1",
                    "address_line2",
                    "city",
                    "state",
                    "pincode",
                    "country",
                ],
                as_dict=True,
            )
            if baddress:
                billing_address = {
                    "line1": baddress.get("address_line1", ""),
                    "line2": baddress.get("address_line2", ""),
                    "city": baddress.get("city", ""),
                    "state": baddress.get("state", ""),
                    "postal_code": baddress.get("pincode", ""),
                    "country": baddress.get("country", ""),
                }

        # Fetch shipping address if available
        if quotation.shipping_address_name:
            # saddress = frappe.db.get_value(
            #     "Address",
            #     quotation.shipping_address_name,
            #     [
            #         "address_line1",
            #         "address_line2",
            #         "city",
            #         "state",
            #         "pincode",
            #         "country",
            #     ],
            #     as_dict=True,
            # )
            saddress = frappe.get_doc("Address",quotation.shipping_address_name)
            if saddress:
                if quotation.custom_delivery_method == "Home Delivery":
                    shipping_address_string = ", ".join(
                        [
                            saddress.get("address_line1") or "",
                            saddress.get("address_line2") or "",
                            saddress.get("city") or "",
                            saddress.get("state") or "",
                            saddress.get("pincode") or "",
                            saddress.get("country") or "",
                        ]
                    ).strip(", ")
                    latitude, longitude = get_geolocation_from_address(shipping_address_string)
                    saddress.custom_latitude = latitude
                    saddress.custom_longitude = longitude
                    saddress.save(ignore_permissions = True)

                shipping_address = {
                    "line1": saddress.get("address_line1", ""),
                    "line2": saddress.get("address_line2", ""),
                    "city": saddress.get("city", ""),
                    "state": saddress.get("state", ""),
                    "postal_code": saddress.get("pincode", ""),
                    "country": saddress.get("country", ""),
                }

        ip_address = frappe.request.headers.get(
            "X-Forwarded-For"
        ) or frappe.request.headers.get("Remote-Addr")

        logger.info("IPAddress : ", ip_address)
        # Create a Stripe PaymentIntent
        amount_in_cents = int(float(quotation_total) * 100)

        # search for existing payment_intent
        payment_intent = search_payment_intent(
            quotation.name, quotation.contact_display
        )

        if payment_intent:
            if payment_intent.get("amount") != amount_in_cents:
                update_payment_intent(
                    payment_intent.get("id"),
                    payment_intent.get("metadata"),
                    amount_in_cents,
                )

        else:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_in_cents,
                currency=quotation.currency,
                metadata={
                    "integration_check": "accept_a_payment",
                    "quotation_id": quotation.name,
                    "customer_id": quotation.contact_display,
                    "session_id": quotation.custom_session_id,
                    "customer_email": quotation.contact_email,
                    "payment_method": payment_method,
                    "ip_address": ip_address,  # Include IP address in metadata
                },
                automatic_payment_methods={
                    "enabled": True,
                },
                description=description,
                receipt_email=customer_email,  # Include customer email
                shipping={
                    "name": customer_name,  # Include customer name in shipping
                    "address": {
                        "line1": shipping_address.get("line1", ""),
                        "line2": shipping_address.get("line2", ""),
                        "city": shipping_address.get("city", ""),
                        "state": shipping_address.get("state", ""),
                        "postal_code": shipping_address.get("postal_code", ""),
                        "country": shipping_address.get("country", ""),
                    },
                },
            )
        frappe.response["data"] = {
            "status": "success",
            "intent_id": payment_intent["id"],
            "client_secret": payment_intent["client_secret"],
            "quotation_name": quotation.name,
        }
    except stripe.error.StripeError as e:
        frappe.log_error(f"Stripe Error: {str(e)}", "Stripe Error")
        frappe.throw(_("Error creating payment intent: {0}").format(e.user_message))
    except frappe.AuthenticationError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Authentication error", "error": str(e)}
    except Exception as e:
        frappe.log_error(f"Unexpected Error: {str(e)}", "Unexpected Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An error occurred while creating the payment intent.",
            "error": str(e),
        }


def search_payment_intent(quotation_id=None, customer_id=None):
    try:
        # Create the query string based on metadata and status
        query_parts = []
        if quotation_id:
            query_parts.append(f"metadata['quotation_id']:'{quotation_id}'")
        if customer_id:
            query_parts.append(f"metadata['customer_id']:'{customer_id}'")

        query = " AND ".join(query_parts)

        # Add status 'incomplete' filter
        query += " AND status:'requires_payment_method'"

        # # Search for payment intents
        # payment_intents = stripe.PaymentIntent.search(
        #     query=query,
        #     limit=1  # Limit the number of results, can be adjusted as needed
        # )

        # Return the result
        # return payment_intents[0]
        return search_payment_intent_raw(query)

    except Exception as e:
        print(f"Error: {e}")
        frappe.log_error(f"Search Payment Intent Error : {str(e)}", "Unexpected Error")
        return None


def search_payment_intent_raw(query):
    stripe_keys = get_stripe_keys()
    url = "https://api.stripe.com/v1/payment_intents/search"
    headers = {
        "Authorization": f'Bearer {stripe_keys.get("secret_key")}',
        "Content-Type": "application/x-www-form-urlencoded",
    }
    params = {"query": query}  # This is your search query string

    try:
        # Make the request
        response = requests.get(url, headers=headers, params=params)

        # Raise an error if the request failed
        response.raise_for_status()

        # Parse the response data
        data = response.json()

        # Return the first payment intent if it exists
        if data and "data" in data and len(data["data"]) > 0:
            return data["data"][0]
        else:
            print("No payment intents found.")
            return None

    except requests.exceptions.HTTPError as err:
        print(f"HTTP error occurred: {err}")
        return None
    except Exception as err:
        print(f"Other error occurred: {err}")
        return None


def update_payment_intent(payment_intent_id, metadata=None, amount=None):
    try:
        # Update the payment intent
        updated_payment_intent = stripe.PaymentIntent.modify(
            payment_intent_id,
            metadata=metadata,  # You can add or update metadata here
            amount=amount,  # Optional: update amount (if needed)
        )
        return updated_payment_intent
    except stripe.error.StripeError as e:
        print(f"Error updating payment intent: {e}")
        return None


@frappe.whitelist(allow_guest=True)
def stripe_webhook():
    payload = frappe.request.data
    sig_header = frappe.request.headers.get("Stripe-Signature")
    # Your webhook secret
    endpoint_secret = "whsec_bf4cd90ee112427471f8ba100ddb38837c447f9529c4e9071f931fe889986286"
    set_session_user("administrator")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError as e:
        # Invalid payload
        frappe.log_error(f"Invalid payload: {str(e)}", "Stripe Webhook Error")
        return {
            "status": "invalid payload"
        }, 400  # Return JSON response with status code 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        frappe.log_error(f"Invalid signature: {str(e)}", "Stripe Webhook Error")
        return {
            "status": "invalid signature"
        }, 400  # Return JSON response with status code 400
    except Exception as e:
        # General exception
        frappe.log_error(f"Unexpected error: {str(e)}", "Stripe Webhook Error")
        return {"status": "error"}, 400  # Return JSON response with status code 400

    # Handle successful payment intent
    try:
        if event["type"] == "payment_intent.succeeded":
            payment_intent = event["data"]["object"]
            # sales_order_name = payment_intent["metadata"].get("sales_order")
            quotation_name = payment_intent["metadata"].get("quotation_id")
            payment_method = payment_intent["metadata"].get("payment_method")

            if quotation_name:
                # Proceed with order placement and payment handling
                process_order_after_payment_success(
                    quotation_name, payment_intent, payment_method
                )
            else:
                frappe.log_error(
                    "sales order name is missing in payment intent metadata",
                    "Stripe Webhook Error",
                )
                return {
                    "status": "missing quotation id"
                }, 400  # Return JSON response with status code 400
    except Exception as e:
        # Handle errors in placing the order or payment
        frappe.log_error(f"Error processing payment: {str(e)}", "Stripe Webhook Error")
        return {
            "status": "error processing payment"
        }, 500  # Return JSON response with status code 500

    return {"status": "success"}, 200  # Return JSON response with status code 200


def process_order_after_payment_success(quotation_name, payment_intent, payment_method):
    try:
        # sales_order = frappe.get_doc("Sales Order", sales_order_name)
        quotation = frappe.get_doc("Quotation", quotation_name)

        cart_settings = frappe.get_cached_doc("Webshop Settings")
        quotation.company = cart_settings.company

        if quotation.custom_delivery_slot:
            delivery_slot = frappe.get_doc("Delivery Slot", quotation.custom_delivery_slot)

        quotation.flags.ignore_permissions = True
        quotation.submit()

        if quotation.quotation_to == "Lead" and quotation.party_name:
            # company used to create customer accounts
            frappe.defaults.set_user_default("company", quotation.company)

        customer_group = cart_settings.default_customer_group

        sales_order = frappe.get_doc(
            _make_sales_order(
                quotation.name, ignore_permissions=True
            )
        )
        sales_order.payment_schedule = []

        if not cint(cart_settings.allow_items_not_in_stock):
            for item in sales_order.get("items"):
                item.warehouse = frappe.db.get_value(
                    "Website Item", {"item_code": item.item_code}, "website_warehouse"
                )
                is_stock_item = frappe.db.get_value(
                    "Item", item.item_code, "is_stock_item"
                )

                if is_stock_item:
                    item_stock = get_web_item_qty_in_stock(
                        item.item_code, "website_warehouse"
                    )
                    if not cint(item_stock.in_stock):
                        throw(_("{0} Not in Stock").format(item.item_code))
                    if item.qty > item_stock.stock_qty:
                        throw(
                            _("Only {0} in Stock for item {1}").format(
                                item_stock.stock_qty, item.item_code
                            )
                        )
        # Adding Delivery Method, Delivery Date And Delivery Slots data
        sales_order.custom_delivery_method = quotation.custom_delivery_method
        if quotation.custom_delivery_slot:
            sales_order.delivery_date, sales_order.custom_delivery_slot = (
                get_date_and_time_slot(delivery_slot)
            )
        elif quotation.custom_store_pickup_datetime:
            sales_order.delivery_date = quotation.custom_store_pickup_datetime.strftime('%y-%m-%d')

        sales_order.custom_payment_method = payment_method

        sales_order.custom_payment_reference = payment_intent.id
        sales_order.flags.ignore_permissions = True
        sales_order.save()
        sales_order.submit()

        # Create Sales Invoice for the Sales Order
        # sales_invoice = create_sales_invoice(sales_order)

        # Submit the Sales Invoice
        # sales_invoice.submit()

        # Create Delivery Note for the Sales Order
        # delivery_note = create_delivery_note(sales_order, sales_invoice)

        # Submit the Delivery Note
        # delivery_note.submit()

        # Create Payment Entry for the Sales Order
        create_payment_entry_with_so(sales_order, payment_intent)

        # sales_order.append(
        #         "references",
        #         {
        #             "reference_doctype": "Payment Entry",
        #             "reference_name": payment_entry.name
        #         },
        #     )

        # sales_order.save()

        # # Adjusting docs status
        frappe.db.set_value("Sales Order", sales_order.name, "status", "To Deliver")
        # # frappe.db.set_value("Delivery Note", delivery_note.name, "status", "To Deliver")
        # frappe.db.commit()

        if hasattr(frappe.local, "cookie_manager"):
            frappe.local.cookie_manager.delete_cookie("cart_count")

        return sales_order.name

    except frappe.exceptions.ValidationError as e:
        frappe.log_error(f"Validation Error: {str(e)}", "Order Placement Error")
        frappe.throw(_("Order placement failed: {0}").format(str(e)))

    except Exception as e:
        frappe.log_error(f"Unexpected error: {str(e)}", "Order Placement Error")
        frappe.throw(
            _("An unexpected error occurred while placing the order. Please try again.")
        )


def create_sales_invoice(sales_order):
    sales_invoice = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "customer": sales_order.customer,
            "due_date": frappe.utils.nowdate(),
            "company": sales_order.company,
            "items": [
                {
                    "item_code": item.item_code,
                    "qty": item.qty,
                    "rate": item.rate,
                    "sales_order": sales_order.name,
                    "warehouse": item.warehouse,
                }
                for item in sales_order.items
            ],
            "debit_to": frappe.db.get_value(
                "Company", sales_order.company, "default_receivable_account"
            ),
            "is_pos": 0,
        }
    )

    # Include taxes and other charges from the Sales Order
    if sales_order.get("taxes"):
        for tax in sales_order.taxes:
            sales_invoice.append(
                "taxes",
                {
                    "charge_type": tax.charge_type,
                    "account_head": tax.account_head,
                    "description": tax.description,
                    "rate": tax.rate,
                    "tax_amount": tax.tax_amount,
                    "cost_center": tax.cost_center,
                    "included_in_print_rate": tax.included_in_print_rate,
                },
            )

    sales_invoice.flags.ignore_permissions = True
    sales_invoice.insert()
    return sales_invoice


def create_delivery_note(sales_order, sales_invoice):
    # Create a dictionary to map Sales Order Item based on item_code and qty to Sales Invoice Item
    sales_invoice_item_map = {
        (inv_item.item_code, inv_item.qty): inv_item.name
        for inv_item in sales_invoice.items
    }
    delivery_note = frappe.get_doc(
        {
            "doctype": "Delivery Note",
            "customer": sales_order.customer,
            "posting_date": frappe.utils.nowdate(),
            "company": sales_order.company,
            "items": [
                {
                    "item_code": item.item_code,
                    "qty": item.qty,
                    "rate": item.rate,
                    "sales_order": sales_order.name,
                    "warehouse": item.warehouse,
                    "against_sales_order": sales_order.name,  # Link each item to Sales Order
                    "so_detail": item.name,  # Link to the specific Sales Order item row (so_detail)
                    "against_sales_invoice": sales_invoice.name,  # Link to Sales Invoice
                    "si_detail": sales_invoice_item_map.get((item.item_code, item.qty)),
                }
                for item in sales_order.items
            ],
        }
    )

    # Include taxes and other charges from the Sales Order
    if sales_order.get("taxes"):
        for tax in sales_order.taxes:
            delivery_note.append(
                "taxes",
                {
                    "charge_type": tax.charge_type,
                    "account_head": tax.account_head,
                    "description": tax.description,
                    "rate": tax.rate,
                    "tax_amount": tax.tax_amount,
                    "cost_center": tax.cost_center,
                    "included_in_print_rate": tax.included_in_print_rate,
                },
            )

    delivery_note.flags.ignore_permissions = True
    delivery_note.insert()
    return delivery_note


def create_payment_entry(sales_invoice, payment_intent, delivery_note=None):
    try:

        # payment_entry = frappe.new_doc("Payment Entry")
        # payment_entry.payment_type = "Receive"
        # payment_entry.party_type = "Customer"
        # payment_entry.party = sales_invoice.customer
        # payment_entry.company = sales_invoice.company
        # payment_entry.reference_no = payment_intent.get(
        #     "id"
        # )  # Use Stripe PaymentIntent ID
        # payment_entry.reference_date = frappe.utils.today()

        # # Set the paid_to account (Stripe account)
        # payment_entry.paid_to = (
        #     "1201 - Stripe FT - CMJ"  # Account used for Stripe payments
        # )
        # payment_entry.paid_to_account_currency = frappe.get_value(
        #     "Account", "1201 - Stripe FT - CMJ", "account_currency"
        # )

        # # Stripe amount is in cents, so we divide by 100 to get the correct amount
        # received_amount = payment_intent.get("amount_received") / 100
        # payment_entry.paid_amount = received_amount
        # payment_entry.received_amount = received_amount
        # payment_entry.mode_of_payment = "Stripe"

        # # Link the payment entry to the sales order in the references table
        # payment_entry.append(
        #     "references",
        #     {
        #         "reference_doctype": "Sales Invoice",
        #         "reference_name": sales_order.name,
        #         "total_amount": sales_order.grand_total,
        #         "outstanding_amount": sales_order.grand_total,
        #         "allocated_amount": received_amount,
        #     },
        # )

        # Create a Payment Entry for Sales Invoice
        payment_entry = frappe.get_doc(
            {
                "doctype": "Payment Entry",
                "payment_type": "Receive",
                "company": sales_invoice.company,
                "posting_date": frappe.utils.nowdate(),
                "party_type": "Customer",
                "party": sales_invoice.customer,
                "paid_to": (
                    # "1201 - Stripe FT - KN"  # Account used for Stripe payments
                    "1201 - Stripe FT - CMJ"  # Account used for Stripe payments
                ),  # Bank account where the payment is received
                "mode_of_payment": "Stripe",
                "paid_amount": sales_invoice.rounded_total or sales_invoice.grand_total,
                "received_amount": sales_invoice.rounded_total
                or sales_invoice.grand_total,
                "reference_no": payment_intent.get(
                    "id"
                ),  # Use payment_intent as reference for payment
                "reference_date": frappe.utils.nowdate(),
                "references": [
                    {
                        "reference_doctype": "Sales Invoice",
                        "reference_name": sales_invoice.name,
                        "total_amount": sales_invoice.rounded_total
                        or sales_invoice.grand_total,
                        "outstanding_amount": sales_invoice.outstanding_amount,
                        "allocated_amount": sales_invoice.rounded_total
                        or sales_invoice.grand_total,
                    },
                ],
                "remarks": f"Payment received via Stripe for Sales Invoice {sales_invoice.name}",
            }
        )

        # If there's a linked Delivery Note, add it to the references
        if delivery_note:
            payment_entry.append(
                "references",
                {
                    "reference_doctype": "Delivery Note",
                    "reference_name": delivery_note,
                    "total_amount": 0,  # Delivery Note doesn't affect payment directly
                    "outstanding_amount": 0,
                    "allocated_amount": 0,
                },
            )

        # Log the references
        logger.debug(f"References: {payment_entry.references}")
        payment_entry.target_exchange_rate = 1

        payment_entry.flags.ignore_permissions = True
        payment_entry.insert()
        payment_entry.submit()

        # Update the Sales Invoice status to "Paid"
        frappe.db.set_value("Sales Invoice", sales_invoice.name, "status", "Paid")

        # frappe.db.set_value("Delivery Note", delivery_note.name, "status", "Paid")

        frappe.db.commit()

    except frappe.exceptions.ValidationError as e:
        frappe.log_error(f"Validation Error: {str(e)}", "Payment Entry Error")
        frappe.throw(_("Payment entry failed due to validation: {0}").format(str(e)))

    except Exception as e:
        logger.debug("Exception In create_payment_entry")
        frappe.log_error(
            f"Unexpected error while creating payment entry for {sales_invoice.name}. Error: {str(e)}",
            "Payment Entry Error",
        )
        frappe.throw(
            _(
                "An unexpected error occurred while creating the payment entry. Please check the logs and try again."
            )
        )


def create_payment_entry_with_so(sales_order, payment_intent):
    try:
        # Log the references
        logger.debug(f"create_payment_entry_with_so: {sales_order.name}")
        # Prepare values for Payment Entry
        paid_amount = sales_order.rounded_total or sales_order.grand_total
        received_amount = paid_amount
        reference_no = payment_intent.get("id")  # Stripe Payment Intent ID
        company = sales_order.company
        customer = sales_order.customer

        # Create a Payment Entry for the Sales Order
        payment_entry = frappe.get_doc(
            {
                "doctype": "Payment Entry",
                "payment_type": "Receive",
                "company": company,
                "posting_date": frappe.utils.nowdate(),
                "party_type": "Customer",
                "party": customer,
                # "paid_to": "1201 - Stripe FT - KN",  # Stripe account for payments
                "paid_to": "1201 - Stripe FT - CMJ",  # Stripe account for payments
                "mode_of_payment": "Stripe",
                "paid_amount": paid_amount,
                "received_amount": received_amount,
                "reference_no": reference_no,  # Stripe Payment Intent reference
                "reference_date": frappe.utils.nowdate(),
                "remarks": f"Payment received via Stripe for Sales Order {sales_order.name}",
                "references": [
                    {
                        "reference_doctype": "Sales Order",
                        "reference_name": sales_order.name,
                        "total_amount": sales_order.rounded_total
                        or sales_order.grand_total,
                        "outstanding_amount": 0,
                        "allocated_amount": sales_order.rounded_total
                        or sales_order.grand_total,
                    }
                ],
            }
        )
        logger.debug(f"In create_payment_entry_with_so: payment_entry: {payment_entry}")

        # Set exchange rate to avoid currency issues (assuming USD for now)
        payment_entry.target_exchange_rate = 1

        # Insert and submit the Payment Entry document
        payment_entry.flags.ignore_permissions = (
            True  # If necessary to bypass permission restrictions
        )
        payment_entry.insert()
        payment_entry.submit()

        # Update Sales Order with Payment Entry reference (Custom Field: payment_entry)
        # frappe.db.set_value("Sales Order", sales_order.name, "payment_entry", payment_entry.name)

        # Update Sales Order status to "To Deliver"
        sales_order.db_set("per_billed", 100)
        # frappe.db.set_value("Sales Order", sales_order.name, "status", "To Deliver")
        # frappe.db.commit()

        return payment_entry.name

    except frappe.exceptions.ValidationError as e:
        frappe.log_error(f"Validation Error: {str(e)}", "Payment Entry Error")
        frappe.throw(_("Payment entry failed due to validation: {0}").format(str(e)))

    except Exception as e:
        logger.debug("Exception in create_payment_entry")
        frappe.log_error(
            f"Unexpected error while creating payment entry for {sales_order.name}. Error: {str(e)}",
            "Payment Entry Error",
        )
        frappe.throw(
            _(
                "An unexpected error occurred while creating the payment entry. Please check the logs and try again."
            )
        )


def set_session_user(user):
    # if not frappe.has_permission("User", "write"):
    #     frappe.throw(_("Permission Denied"), frappe.PermissionError)

    frappe.local.session.user = user
    frappe.local.session.data.user = user
    frappe.local.session.data.user_type = (
        "System User"  # Set to "System User" if needed
    )
    # frappe.local.session.user_id = frappe.get_doc("User", user).id

    # Optional: Refresh permissions for the new user context
    frappe.local.user = user
    frappe.local.session.user_info = frappe.get_doc("User", user)
    frappe.local.session.user_roles = frappe.get_roles(user)


@frappe.whitelist(allow_guest=True)
def update_cart_details(cart, session_id=None,):
    try:
        # Check if Authorization header is present
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)

        # Validate authorization via API keys
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw(
                "Authorization header is malformed or missing API keys.",
                frappe.AuthenticationError,
            )

        validate_auth_via_api_keys(api_keys)

        # Check if the user is logged in
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            if session_id is None:
                frappe.throw("Guest user must provide session ID.", frappe.DataError)
        
        if session_id:
            frappe.set_user("Guest")

        # Get the party (customer)
        party = get_party()

        # Fetch or create the quotation (cart)
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            quotation = frappe.get_all(
                "Quotation",
                filters={"custom_session_id": session_id, "docstatus": 0},
                limit=1,
            )
            if quotation:
                quotation = frappe.get_doc("Quotation", quotation[0].name)
            else:
                frappe.throw("Cart is empty!", frappe.ValidationError)
        else:
            quotation = _get_cart_quotation(party)

        # Check if there are no items in the quotation
        if not quotation.items or len(quotation.items) == 0:
            frappe.throw("Cart is empty!", frappe.ValidationError)

        # quotation = _get_cart_quotation()
        cart_settings = frappe.get_cached_doc("Webshop Settings")
        quotation.company = cart_settings.company

        # Check if 'contact_name' is provided and not null, then update
        contact_name = cart.get("contact_name")
        if contact_name:
            quotation.contact_display = contact_name

        contact_mobile = cart.get("contact_mobile")
        if contact_mobile:
            quotation.contact_mobile = contact_mobile
         
        contact_email = cart.get("contact_email")
        if contact_email:
            quotation.contact_email = contact_email

        billing_address = cart.get("billing_address")
        if billing_address:
            # Update or create the Billing Address document
            billing_address_doc = (
                frappe.get_doc("Address", quotation.customer_address)
                if quotation.customer_address
                else frappe.new_doc("Address")
            )
            if billing_address_doc.address_title is None:
                billing_address_doc.address_title = (
                    quotation.name + " - Billing Address"
                )
            billing_address_doc.address_line1 = billing_address.get("address_line1")
            billing_address_doc.address_line2 = billing_address.get("address_line2")
            billing_address_doc.city = billing_address.get("city")
            billing_address_doc.state = billing_address.get("state")
            billing_address_doc.pincode = billing_address.get("pincode")
            billing_address_doc.country = billing_address.get("country")
            billing_address_doc.save(ignore_permissions=True)

            quotation.customer_address = billing_address_doc.name

        shipping_address = cart.get("shipping_address")
        if shipping_address:
            # Update or create the Shipping Address document
            shipping_address_doc = (
                frappe.get_doc("Address", quotation.shipping_address_name)
                if quotation.shipping_address_name
                else frappe.new_doc("Address")
            )
            if shipping_address_doc.address_title is None:
                shipping_address_doc.address_title = (
                    quotation.name + " - Shipping Address"
                )
            shipping_address_doc.address_line1 = shipping_address.get("address_line1")
            shipping_address_doc.address_line2 = shipping_address.get("address_line2")
            shipping_address_doc.city = shipping_address.get("city")
            shipping_address_doc.state = shipping_address.get("state")
            shipping_address_doc.pincode = shipping_address.get("pincode")
            shipping_address_doc.country = shipping_address.get("country")
            shipping_address_doc.save(ignore_permissions=True)

            quotation.shipping_address_name = shipping_address_doc.name

        delivery_option = cart.get("delivery_option")
        if delivery_option:
            if delivery_option.get("delivery_method"):
                quotation.custom_delivery_method = delivery_option.get(
                    "delivery_method"
                )
                if delivery_option.get(
                    "delivery_method"
                ) == "Home Delivery" and delivery_option.get("delivery_type"):
                    quotation.custom_delivery_type = delivery_option.get(
                        "delivery_type"
                    )
                elif delivery_option.get(
                    "delivery_method"
                ) == "Store Pickup" and delivery_option.get("store"):
                    quotation.custom_pickup_store = delivery_option.get("store")
                    quotation.custom_store_pickup_datetime = delivery_option.get(
                        "store_pickup_time"
                    )
            if delivery_option.get("delivery_slot"):
                delivery_slot_name = delivery_option.get("delivery_slot")
                delivery_slot = frappe.get_doc("Delivery Slot", delivery_slot_name)
                quotation.custom_delivery_slot = delivery_slot.name
            else:
                delivery_slot = None

        # quotation.shipping_rule = frappe.get_doc("Shipping Rule", "Store Pickup")
        # _apply_shipping_rule(party, quotation, cart_settings)
        apply_cart_settings(quotation=quotation)
        quotation.flags.ignore_permissions = True
        quotation.save()

        # frappe.db.set_value("Quotation", quotation.name, "shipping_rule", "Store Pickup")
        # frappe.db.commit()

        frappe.local.response["http_status_code"] = HTTPStatus.OK
        frappe.response["data"] = {
            "status": "success",
            "message": _("Cart details updated successfully"),
        }

    except Exception as e:
        # Rollback the transaction in case of any error
        frappe.db.rollback()
        # Log the error for debugging
        frappe.log_error(frappe.get_traceback(), "Place Order Failed")
        # Raise the error to inform the client
        frappe.throw(_("There was an error processing the order: {0}").format(str(e)))


@frappe.whitelist(allow_guest=True, methods="GET")
def get_pickup_store():
    try:
        # Check if Authorization header is present
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)

        # Validate authorization via API keys
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw(
                "Authorization header is malformed or missing API keys.",
                frappe.AuthenticationError,
            )

        # Call the custom validation function for API keys
        validate_auth_via_api_keys(api_keys)

        # Fetch warehouses where warehouse_type is 'Pickup Point'
        pickup_points = frappe.get_all(
            "Warehouse",
            filters={"warehouse_type": "Pickup Point", "disabled": 0},
            fields=[
                "name",
                "warehouse_name",
                "address_line_1",
                "address_line_2",
                "city",
                "state",
                "pin",
            ],
        )

        for pickup_point in pickup_points:
            # Fetch working hours from the child table linked to the Warehouse
            working_hours = frappe.get_all(
                "Warehouse Working Hours",
                filters={"parent": pickup_point.name},
                fields=["day_of_week", "start_time", "end_time"],
            )

            # Add working hours to the warehouse
            pickup_point["working_hours"] = working_hours

        # Return the list of pickup points with working hours
        frappe.local.response["http_status_code"] = HTTPStatus.OK
        frappe.response["data"] = {"status": "success", "pickup_points": pickup_points}

    except frappe.AuthenticationError as e:
        # Handle authentication errors (invalid/missing API keys)
        frappe.local.response["http_status_code"] = HTTPStatus.UNAUTHORIZED
        frappe.response["data"] = {"status": "error", "message": str(e)}

    except frappe.PermissionError as e:
        # Handle permission errors
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"status": "error", "message": str(e)}

    except Exception as e:
        # Handle general exceptions
        frappe.log_error(frappe.get_traceback(), "Error in get_pickup_store API")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "status": "error",
            "message": "There was an error processing the request.",
        }


@frappe.whitelist(allow_guest=True, methods="GET")
def get_delivery_slot(delivery_type=None):
    try:
        # Check if Authorization header is present
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)

        # Validate authorization via API keys
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw(
                "Authorization header is malformed or missing API keys.",
                frappe.AuthenticationError,
            )

        # Call the custom validation function for API keys
        validate_auth_via_api_keys(api_keys)

        # Define the allowed delivery types
        allowed_delivery_type = ["Express Delivery", "Standard Delivery"]

        # Check if the delivery type is valid
        if delivery_type not in allowed_delivery_type:
            frappe.throw("Delivery type not supported", frappe.ValidationError)

        if delivery_type == "Express Delivery":
            day_name = get_day_name(today())
            current_time = (
                now()
            )  # Get the current time for filtering Express Delivery slots
        else:
            day_name = get_day_name(add_days(today(), 1))
            current_time = (
                None  # No need to filter based on current time for Normal Delivery
            )

        # Build filters for the delivery slots query
        filters = {"parent": "Zone 1", "day": day_name}

        # If it's Express Delivery, filter slots with start_time greater than the current time
        if delivery_type == "Express Delivery":
            delivery_slots = frappe.db.sql(
                """
                SELECT name, day, start_time, end_time
                FROM `tabDelivery Slot`
                WHERE parent = %s
                AND day = %s
                AND start_time > %s
            """,
                ("Zone 1", day_name, current_time),
                as_dict=True,
            )
            # Append the current date or any specific date to each slot entry
            for slot in delivery_slots:
                slot["date"] = today()
        else:
            # For Standard Delivery, no need to filter by start_time
            delivery_slots = frappe.get_all(
                "Delivery Slot",
                filters=filters,
                fields=["name", "day", "start_time", "end_time"],
            )
            # Append the current date or any specific date to each slot entry
            for slot in delivery_slots:
                slot["date"] = frappe.utils.add_days(today(), 1)

        # Return the list of delivery slots
        frappe.local.response["http_status_code"] = HTTPStatus.OK
        frappe.response["data"] = {
            "status": "success",
            "delivery_slots": delivery_slots,
        }

    except frappe.AuthenticationError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.UNAUTHORIZED
        frappe.response["data"] = {"status": "error", "message": str(e)}

    except frappe.PermissionError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"status": "error", "message": str(e)}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error in get_delivery_slot API")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True, methods="POST")
def fetch_cart(session_id=None):
    try:
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Check if the user is logged in
        if frappe.local.session.user is None or frappe.session.user == "Guest":
            if session_id is None:
                frappe.throw("Should include session id for Guest user.")

        # Get the party (customer)
        party = get_party()

        # Fetch or create the quotation (cart)

        if frappe.local.session.user is None or frappe.session.user == "Guest":
            quotation = frappe.get_all(
                "Quotation",
                filters={"custom_session_id": session_id, "docstatus": 0},
                limit=1,
            )
            if quotation:
                quotation = frappe.get_doc("Quotation", quotation[0].name)
            else:
                frappe.throw("Cart is emplty!!")
        else:
            quotation = _get_cart_quotation(party)

        # Prepare the response data
        cart_response = []
        for cart_item in quotation.items:
            # item_details = frappe.get_doc("Item", cart_item.item_code)
            formatted_item = {
                "_id": cart_item.item_code,
                "count": cart_item.qty,
                "item": {
                    # "formatted_mrp": cart_item.formatted_mrp,
                    # "formatted_price": cart_item.formatted_price,
                    # "has_variants": cart_item.has_variants,
                    "in_cart": True,
                    # "in_stock": cart_item.in_stock,
                    "item_code": cart_item.item_code,
                    "item_group": cart_item.item_group,
                    "item_name": cart_item.item_name,
                    "name": cart_item.name,
                    # "on_backorder": cart_item.on_backorder,
                    "price_list_rate": cart_item.price_list_rate,
                    # "ranking": cart_item.ranking,
                    # "rating": cart_item.rating,
                    # "route": cart_item.route,
                    # "short_description": cart_item.short_description,
                    # "variant_of": cart_item.variant_of,
                    "web_item_name": cart_item.item_name,
                    # "web_long_description": item_details.web_long_description,
                    "website_image": cart_item.image,
                    # "website_warehouse": item_details.website_warehouse,
                    "wished": False,
                },
            }
            cart_response.append(formatted_item)

        # Return the formatted response
        return cart_response

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Fetch Cart Error")
        frappe.throw(
            _("An error occurred while fetching the cart. Please try again later."),
            frappe.ValidationError,
        )


def get_day_name(date_str):
    # Convert date string to a datetime object
    date_obj = getdate(date_str)
    # Get the day name
    return calendar.day_name[date_obj.weekday()]


def get_geolocation_from_address(address):
    try:
        # Fetch Google API key from the Google Settings Doctype
        google_api_key = frappe.db.get_single_value('Google Settings', 'api_key')
        # Check if the API key was retrieved successfully
        if not google_api_key:
            frappe.throw(_("Google API Key not found in Google Settings"))

        # Google Geocoding API endpoint
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={google_api_key}"

        # Send a request to the Google Geocoding API
        response = requests.get(url)
        data = response.json()

        if data["status"] == "OK":
            # Extract the latitude and longitude from the response
            location = data["results"][0]["geometry"]["location"]
            latitude = location["lat"]
            longitude = location["lng"]
            return latitude, longitude
        else:
            return f"Error: Unable to get the geolocation. {data['status']}"

    except Exception as e:
        return f"An error occurred: {str(e)}"
