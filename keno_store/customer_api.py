from http import HTTPStatus
import frappe
from frappe import _
from frappe.auth import validate_auth_via_api_keys
from frappe.utils.data import cint
from keno_store.cart_api import _get_cart_quotation, apply_cart_settings, set_cart_count


@frappe.whitelist(allow_guest=True, methods=["POST"])
def update_user_email(new_email):
    """API for updating the email address of the logged-in user"""
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.", frappe.PermissionError) 

        # Get the current user
        user = frappe.local.session.user

        # Get the current email of the user
        old_email = frappe.db.get_value("User", user, "email")

        if old_email == new_email:
            frappe.throw("Using old email", frappe.ValidationError)

        # Ensure the user is not a guest
        if user is None or user == "Guest":
            frappe.throw("Guests cannot update their email", frappe.PermissionError)

        # Validate the new email format
        if not frappe.utils.validate_email_address(new_email):
            frappe.throw("Invalid email address format", frappe.ValidationError)

        # Check if the new email is already taken by another user
        if frappe.db.exists("User", {"email": new_email}):
            frappe.throw(
                "This email is already associated with another user",
                frappe.DuplicateEntryError,
            )

        # Rename the User document (change the DocType name to the new email)
        frappe.rename_doc("User", old_email, new_email, force=True)

        # Update the user's email in the new User document
        frappe.db.set_value("User", new_email, "email", new_email)
        frappe.db.set_value("User", new_email, "username", new_email)  # Update login ID

        # Update the user's email address
        frappe.db.set_value("User", user, "email", new_email)

        # Update the email in the associated Contact document, if any
        update_contact_email(old_email, new_email)

        # Update the email in the associated Customer document, if any
        update_customer_email(old_email, new_email)

        frappe.response["data"] = {
            "status": "success",
            "message": ("Email address has been updated successfully."),
        }

    except frappe.ValidationError as e:
        frappe.local.response["http_status_code"] = 403
        frappe.response["data"] = {
            "status": "fail",
            "message": ("Validation error: {0}").format(str(e)),
        }
    except frappe.DuplicateEntryError as e:
        frappe.local.response["http_status_code"] = 409
        frappe.response["data"] = {
            "status": "fail",
            "message": ("Duplicate Value error: {0}").format(str(e)),
        }
    except frappe.PermissionError as e:
        frappe.local.response["http_status_code"] = 404
        frappe.response["data"] = {
            "status": "fail",
            "message": ("Permission denied: {0}").format(str(e)),
        }
    except Exception as e:
        frappe.local.response["http_status_code"] = 500
        frappe.response["data"] = {
            "status": "fail",
            "message": ("An error occurred: {0}").format(str(e)),
        }


def update_contact_email(old_email, new_email):
    """Helper function to update the contact email"""
    contacts = frappe.get_all("Contact", filters={"email_id": old_email})

    for contact in contacts:
        frappe.db.set_value("Contact", contact.name, "email_id", new_email)


def update_customer_email(old_email, new_email):
    """Helper function to update the customer email"""
    customers = frappe.get_all("Customer", filters={"email_id": old_email})

    for customer in customers:
        frappe.db.set_value("Customer", customer.name, "email_id", new_email)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_own_customer_profile():
    """
    Custom API to get the logged-in customer's profile from the Customer Doctype.
    Returns first name, last name, mobile number, address, and email.
    Includes exception handling for various scenarios.
    """
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Get the current user
        user = frappe.local.session.user

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.", frappe.PermissionError) 

        # Ensure the user is not a guest
        if user == "Guest":
            frappe.throw(
                "You need to be logged in to view your profile.", frappe.PermissionError
            )

        # Fetch the customer linked with the logged-in user's email
        customer = frappe.db.get_value(
            "Customer",
            {"email_id": user},
            ["name", "customer_name", "mobile_no", "email_id", "customer_primary_address"],
            as_dict=True,
        )

        if not customer:
            frappe.throw(
                "Customer profile not found for this user.", frappe.DoesNotExistError
            )

        # Fetch customer's primary address
        # primary_address = frappe.get_doc("Address", customer["customer_primary_address"])
        address_list = frappe.get_all(
            "Address",
            filters={"name": customer["customer_primary_address"]},
            fields=["address_line1", "address_line2", "city", "state", "pincode", "country"],
            limit=1,
        )

        # Prepare profile data
        profile_data = {
            "first_name": (
                customer["customer_name"].split(" ")[0]
                if " " in customer["customer_name"]
                else customer["customer_name"]
            ),
            "last_name": (
                customer["customer_name"].split(" ")[1]
                if " " in customer["customer_name"]
                else ""
            ),
            "mobile_no": customer["mobile_no"],
            "email": customer["email_id"],
            "address": address_list[0] if address_list else None,
        }

        # Return profile data
        frappe.response["data"] = {"status": "success", "profile": profile_data}

    except frappe.PermissionError as e:
        # Handle permission errors (e.g., guest user trying to access)
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}

    except frappe.DoesNotExistError as e:
        # Handle case where no customer profile is found
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        frappe.response["data"] = {
            "message": "Requested document does not exist",
            "error": str(e),
        }

    except Exception as e:
        # Handle any unexpected errors
        frappe.log_error(frappe.get_traceback(), "Get Customer Profile API Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def manage_customer_profile(profile=None):
    """
    Custom API to get or update the logged-in customer's profile from the Customer Doctype.
    Handles first name, last name, mobile number, address, and email.
    Includes exception handling for various scenarios.
    """
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Get the current user
        user = frappe.local.session.user

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("You need to be logged in to view or update your profile.", frappe.PermissionError)

        if frappe.request.method == "GET":
            # Fetch the customer linked with the logged-in user's email
            customer = frappe.db.get_value(
                "Customer",
                {"email_id": user},
                ["name", "customer_name", "mobile_no", "email_id", "customer_primary_address"],
                as_dict=True,
            )

            if not customer:
                frappe.throw("Customer profile not found for this user.", frappe.DoesNotExistError)

            # Fetch customer's primary address
            address_list = frappe.get_all(
                "Address",
                filters={"name": customer["customer_primary_address"]},
                fields=["address_line1", "address_line2", "city", "state", "pincode", "country"],
                limit=1,
            )

            # Prepare profile data
            profile_data = {
                "first_name": (
                    customer["customer_name"].split(" ")[0]
                    if " " in customer["customer_name"]
                    else customer["customer_name"]
                ),
                "last_name": (
                    customer["customer_name"].split(" ")[1]
                    if " " in customer["customer_name"]
                    else ""
                ),
                "mobile_no": customer["mobile_no"],
                "email": customer["email_id"],
                "address": address_list[0] if address_list else None,
            }

            # Return profile data
            frappe.response["data"] = {"status": "success", "profile": profile_data}

        elif frappe.request.method == "POST":
            # Parse the profile JSON data
            profile_data = frappe.local.form_dict.get("profile", {})

            if not profile_data:
                frappe.throw("No profile data provided for update.", frappe.ValidationError)

            first_name = profile_data.get("first_name")
            last_name = profile_data.get("last_name")
            mobile_no = profile_data.get("mobile_no")
            email = profile_data.get("email")
            address = profile_data.get("address", {})

            # Update Customer details
            if not first_name or not last_name or not email:
                frappe.throw("First name, last name, and email are required.", frappe.ValidationError)

            customer_name = f"{first_name} {last_name}"
            customer_doc = frappe.get_doc("Customer", {"email_id": email})
            if not customer_doc:
                frappe.throw("Customer profile not found for this email.", frappe.DoesNotExistError)

            customer_doc.customer_name = customer_name
            customer_doc.mobile_no = mobile_no
            customer_doc.email_id = email
            customer_doc.save(ignore_permissions=True)

            # Update or create the Address document
            if address:
                address_doc = frappe.get_doc("Address", customer_doc.customer_primary_address) if customer_doc.customer_primary_address else frappe.new_doc("Address")
                if address_doc.address_title is None:
                    address_doc.address_title = customer_doc.customer_name + " - Primary Address"
                address_doc.address_line1 = address.get("address_line1")
                address_doc.address_line2 = address.get("address_line2")
                address_doc.city = address.get("city")
                address_doc.state = address.get("state")
                address_doc.pincode = address.get("pincode")
                address_doc.country = address.get("country")
                address_doc.save(ignore_permissions=True)

                customer_doc.customer_primary_address = address_doc.name
                customer_doc.save(ignore_permissions=True)

            # Return success response
            frappe.response["data"] = {"status": "success", "message": "Profile updated successfully."}

    except frappe.PermissionError as e:
        # Handle permission errors (e.g., guest user trying to access)
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}

    except frappe.DoesNotExistError as e:
        # Handle case where no customer profile is found
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        frappe.response["data"] = {
            "message": "Requested document does not exist",
            "error": str(e),
        }

    except frappe.ValidationError as e:
        # Handle validation errors (e.g., missing required fields)
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {"message": "Validation error", "error": str(e)}

    except Exception as e:
        # Handle any unexpected errors
        frappe.log_error(frappe.get_traceback(), "Manage Customer Profile API Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_customer_past_orders():
    """
    Custom API to get the logged-in customer's past orders.
    Returns details such as order ID, date, status, and total amount.
    Includes exception handling for various scenarios.
    """
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Get the current user
        user = frappe.local.session.user

        if user is None or user == "Guest":
            frappe.throw("You need to be logged in to view your orders.", frappe.PermissionError)

        # Fetch the customer linked with the logged-in user's email
        customer = frappe.db.get_value(
            "Customer",
            {"email_id": user},
            ["name"],
            as_dict=True,
        )

        if not customer:
            frappe.throw("Customer profile not found for this user.", frappe.DoesNotExistError)

        # Fetch past orders for the customer
        orders = frappe.get_all(
            "Sales Order",
            filters={"customer": customer["name"], "docstatus": 1},  # Assuming docstatus=1 means completed orders
            fields=["name", "transaction_date", "status", "grand_total"],
            order_by="transaction_date desc"
        )

        # Prepare orders data
        order_data = [
            {
                "order_id": order["name"],
                "date": order["transaction_date"],
                "status": order["status"],
                "total_amount": order["grand_total"]
            }
            for order in orders
        ]

        # Return orders data
        frappe.response["data"] = {"status": "success", "orders": order_data}

    except frappe.PermissionError as e:
        # Handle permission errors (e.g., guest user trying to access)
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}

    except frappe.DoesNotExistError as e:
        # Handle case where no customer profile is found
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        frappe.response["data"] = {
            "message": "Requested document does not exist",
            "error": str(e),
        }

    except Exception as e:
        # Handle any unexpected errors
        frappe.log_error(frappe.get_traceback(), "Get Customer Past Orders API Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_order_details_by_id(order_id):
    """
    Custom API to get details of a specific order by ID.
    Restricts access to only the logged-in customer's orders.
    Returns order details such as date, status, total amount, items, taxes, 
    shipping address, and contact info.
    Includes exception handling for various scenarios.
    """
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Get the current user
        user = frappe.local.session.user

        if user is None or user == "Guest":
            frappe.throw("You need to be logged in to view order details.", frappe.PermissionError)

        # Fetch the customer linked with the logged-in user's email
        customer = frappe.db.get_value(
            "Customer",
            {"email_id": user},
            ["name"],
            as_dict=True,
        )

        if not customer:
            frappe.throw("Customer profile not found for this user.", frappe.DoesNotExistError)

        # Fetch the order details
        order = frappe.get_doc("Sales Order", order_id)

        if not order:
            frappe.throw("Order not found.", frappe.DoesNotExistError)

        # Check if the order belongs to the current customer
        if order.customer != customer["name"]:
            frappe.throw("You do not have permission to access this order.", frappe.PermissionError)

        # Prepare order data
        order_data = {
            "order_id": order.name,
            "date": order.transaction_date,
            "status": order.status,
            "total_amount": order.grand_total,
            "items": [
                {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "quantity": item.qty,
                    "base_price": item.price_list_rate,
                    "price": item.rate,
                    "amount": item.amount
                }
                for item in order.items
            ],
            # Fetch taxes from the Sales Taxes and Charges table
            "taxes": [
                {
                    "tax_type": tax.description,
                    "tax_rate": tax.rate,
                    "tax_amount": tax.tax_amount
                }
                for tax in order.taxes
            ],
            # Fetch shipping address
            "shipping_address": {
                "address_line1": order.shipping_address_name and frappe.db.get_value("Address", order.shipping_address_name, "address_line1"),
                "address_line2": order.shipping_address_name and frappe.db.get_value("Address", order.shipping_address_name, "address_line2"),
                "city": order.shipping_address_name and frappe.db.get_value("Address", order.shipping_address_name, "city"),
                "state": order.shipping_address_name and frappe.db.get_value("Address", order.shipping_address_name, "state"),
                "pincode": order.shipping_address_name and frappe.db.get_value("Address", order.shipping_address_name, "pincode"),
                "country": order.shipping_address_name and frappe.db.get_value("Address", order.shipping_address_name, "country")
            },
            # Fetch contact information
            "contact_info": {
                "contact_name": order.contact_display,
                "contact_mobile": order.contact_mobile
            }
        }

        # Return order data
        frappe.response["data"] = {"status": "success", "order": order_data}

    except frappe.PermissionError as e:
        # Handle permission errors (e.g., unauthorized access)
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}

    except frappe.DoesNotExistError as e:
        # Handle case where the order or customer profile is not found
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        frappe.response["data"] = {
            "message": "Requested document does not exist",
            "error": str(e),
        }

    except Exception as e:
        # Handle any unexpected errors
        frappe.log_error(frappe.get_traceback(), "Get Order Details API Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True, methods=["POST"])
def reorder_quotation(order_id=None):
    """
    Custom API to reorder items from a previous sales order.
    Creates a new quotation based on the old order details.
    Fetches customer, items, address, pricing, and shipping rules.
    Continues adding available items if some are unavailable.
    """

    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Get the current user
        user = frappe.local.session.user

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("You need to be logged in to reorder items.", frappe.PermissionError)

        # Ensure an order ID is provided
        if not order_id:
            frappe.throw("Order ID is required for reordering.", frappe.ValidationError)

        # Fetch the customer linked with the logged-in user's email
        customer = frappe.db.get_value(
            "Customer",
            {"email_id": user},
            ["name"],
            as_dict=True,
        )

        if not customer:
            frappe.throw("Customer profile not found for this user.", frappe.DoesNotExistError)

        # Fetch the previous Sales Order using the order_id
        sales_order = frappe.get_doc("Sales Order", {"name": order_id, "customer": customer["name"]})

        if not sales_order:
            frappe.throw("No such order found for the current user.", frappe.DoesNotExistError)

        # Create a new Quotation for the same customer
        # quotation = frappe.new_doc("Quotation")
        quotation = _get_cart_quotation()
        quotation.party_name = sales_order.customer
        quotation.transaction_date = frappe.utils.nowdate()
        quotation.currency = sales_order.currency
        quotation.conversion_rate = sales_order.conversion_rate
        quotation.quotation_to = "Customer"
        quotation.shipping_rule = sales_order.shipping_rule  # Apply the same shipping rule, if any

        # Use the same shipping and billing address
        quotation.customer_primary_address = sales_order.customer_address
        quotation.shipping_address_name = sales_order.shipping_address_name

        # Track unavailable items
        unavailable_items = []

        # Loop through each item from the previous Sales Order
        for item in sales_order.items:
            try:
                # Step 1: Check if the item is available (not disabled)
                item_status = frappe.db.get_value("Item", {"item_code": item.item_code}, ["disabled", "is_stock_item"])
                if item_status and item_status[0] == 1:
                    unavailable_items.append(f"Item '{item.item_code}' is disabled.")
                    continue  # Skip to the next item

                # Step 2: Check stock availability for stock items
                if item_status and item_status[1] == 1:  # If the item is a stock item
                    available_qty = frappe.db.get_value("Bin", {"item_code": item.item_code, "warehouse": item.warehouse}, "projected_qty")
                    if available_qty < item.qty:
                        unavailable_items.append(f"Item '{item.item_code}' is out of stock. Available: {available_qty}, Required: {item.qty}.")
                        continue  # Skip to the next item

                # # Step 3: Apply Pricing Rule (if any)
                # price_list_rate = get_pricing_rule(item.item_code, quotation.party_name, quotation.transaction_date, item.qty, quotation.currency)

                quotation_items = quotation.get("items", {"item_code": item.item_code})
                if not quotation_items:
                    quotation.append(
                        "items",
                        {
                            "doctype": "Quotation Item",
                            "item_code": item.item_code,
                            "qty": item.qty,
                            "additional_notes": item.additional_notes,
                            "warehouse": item.warehouse,
                        },
                    )
                else:
                    quotation_items[0].qty = item.qty
                    quotation_items[0].warehouse = item.warehouse
                    quotation_items[0].additional_notes = item.additional_notes

            except Exception as item_error:
                # Log any unexpected error related to individual item processing
                unavailable_items.append(f"Error adding item '{item.item_code}': {str(item_error)}")

        # Check if at least one item is available
        if not quotation.items:
            frappe.throw("None of the items from the previous order are available for reorder.")
        
        apply_cart_settings(quotation=quotation)

        quotation.flags.ignore_permissions = True

        # Save the new Quotation
        quotation.save()

        set_cart_count(quotation)

        # Return the Quotation ID and log unavailable items
        return {
            "status": "success",
            "message": "New Quotation created successfully",
            "quotation_id": quotation.name,
            "unavailable_items": unavailable_items
        }

    except frappe.DoesNotExistError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        frappe.response["data"] = {"message": "Order not found", "error": str(e)}

    except frappe.ValidationError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {"message": "Validation error", "error": str(e)}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Reorder Quotation API Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }
