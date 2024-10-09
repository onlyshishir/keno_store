from http import HTTPStatus
import frappe
from frappe import _
from frappe.auth import validate_auth_via_api_keys


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_delivery_notes(status=None, deliveryPartner=None, page=1, page_size=10):
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.", frappe.PermissionError)

        if not status:
            frappe.throw(_("Delivery status is required"), frappe.ValidationError)
            return {
                "status": "error",
                "message": _("Delivery status is required"),
            }
        
        page = int(page)
        page_size = int(page_size)
        if page <= 0 or page_size <= 0:
            frappe.throw(
                _("Invalid page or page size: {0}").format(str(e)),
                frappe.InvalidRequestError,
            )

        # Calculate the offset and limit for pagination
        offset = (page - 1) * page_size
        limit = page_size
        total_delivery_notes = 0
        delivery_notes = []

        if status == 'Ready for Pickup' or status == 'available':
            # Fetch Delivery Notes filtered by custom_delivery_status
            delivery_notes = frappe.get_all(
                "Delivery Note",
                filters={"custom_delivery_status": 'Ready for Pickup', "docstatus":0},
                fields=["name", "posting_date", "customer", "custom_delivery_status as status", "grand_total"],
                limit_start=offset,
                limit_page_length=limit
            )
            # Check if there are more pages
            total_delivery_notes = frappe.db.count(
                "Delivery Note", filters={"custom_delivery_status": status, "docstatus":0}
            )
        elif status != 'Ready for Pickup' and deliveryPartner:
            supplier = get_transporter_supplier_by_user(deliveryPartner)
            if supplier:
                delivery_notes = frappe.get_all(
                    "Delivery Note",
                    filters={"custom_delivery_status": status, "transporter": supplier.name, "docstatus":0},
                    fields=["name", "posting_date", "customer", "custom_delivery_status as status", "grand_total"],
                    limit_start=offset,
                    limit_page_length=limit
                )
                # Check if there are more pages
                total_delivery_notes = frappe.db.count(
                    "Delivery Note", filters={"custom_delivery_status": status, "transporter": supplier.name, "docstatus":0}
                )
        # Loop through each delivery note to fetch the linked sales order
        for note in delivery_notes:
            # Fetch the linked Sales Order using the 'against_sales_order' field from Delivery Note Item
            sales_order = frappe.db.get_value("Delivery Note Item", {"parent": note["name"]}, "against_sales_order")
            order_doc = frappe.get_doc("Sales Order", sales_order)
            note["order_id"] = sales_order
            note["createdAt"] = order_doc.creation.isoformat()
            note["items"] = []
            note["items"].extend([
                {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "quantity": item.qty,
                    "base_price": item.price_list_rate,
                    "price": item.rate,
                    "amount": item.amount,
                }
                for item in order_doc.items
            ])

        total_pages = (total_delivery_notes + page_size - 1) // page_size  # Ceiling division

        frappe.response["data"] = {
            "status": "success",
            "delivery_notes": delivery_notes,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_orders": total_delivery_notes,
                "total_pages": total_pages,
            }
        }

    except frappe.PermissionError as e:
        # Handle permission errors (e.g., guest user trying to access)
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}

    except frappe.ValidationError as e:
        # Handle case where no customer profile is found
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {
            "message": "Validation error",
            "error": str(e),
        }

    except Exception as e:
        # Handle any unexpected errors
        frappe.log_error(frappe.get_traceback(), "Get delivery notes by status API Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


def get_transporter_supplier_by_user(user=None):
    """
    Retrieve the supplier who is a transporter based on the current logged-in portal user or a specified user.

    Args:
        user (str, optional): The user ID or email of the portal user. Defaults to the currently logged-in user.

    Returns:
        dict: Supplier details if a supplier (transporter) is found, otherwise None.
    """
    try:
        # Use the provided user or fall back to the logged-in user
        user = user or frappe.session.user

        # Ensure the user is not a guest
        if user == "Guest":
            frappe.throw("Guest users cannot access supplier details.", frappe.PermissionError)

        supplier_name = frappe.db.get_value("Portal User", {"user": user, "parenttype": "Supplier"}, ["parent"])

        # Fetch the supplier associated with the portal user and check if they are a transporter
        supplier = frappe.db.get_value("Supplier", {"name": supplier_name, "is_transporter": 1}, ["name", "supplier_name", "supplier_group"], as_dict=True)

        # Return the supplier details if found, otherwise return None
        return supplier if supplier else None

    except frappe.PermissionError as e:
        frappe.log_error(frappe.get_traceback(), "Permission error while fetching transporter supplier")
        return None

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error while fetching transporter supplier")
        return None