from http import HTTPStatus
import frappe
from frappe import _
from frappe.auth import validate_auth_via_api_keys

@frappe.whitelist(allow_guest=True, methods=["POST"])
def confirmOrder(delivery_note_id=None, order_id=None, liveLocation=None):
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Ensure the user is logged in and is a delivery partner
        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.", frappe.PermissionError)

        # Check if order_id is provided
        if not delivery_note_id:
            frappe.throw(_("Delivery Note ID is required"), frappe.ValidationError)
            return {
                "status": "error",
                "message": _("Delivery Note ID is required"),
            }

        # Fetch the delivery note using the provided order_id
        delivery_note = frappe.get_doc("Delivery Note", delivery_note_id)

        # Ensure the delivery note exists and is not already assigned
        if not delivery_note or delivery_note.transporter:
            frappe.throw(_("Delivery Note not found or already assigned."), frappe.ValidationError)

        # Assign the current user as the delivery partner
        delivery_note.transporter = frappe.local.session.user
        delivery_note.custom_delivery_status = "Rider Confirmed"  # Update status as required
        delivery_note.save(ignore_permissions=True)  # Save without permissions check

        insert_user_location(frappe.local.session.user, liveLocation)

        frappe.db.commit()  # Commit the changes

        frappe.publish_realtime(
            "orderConfirmed",
            {
                "status": "success",
                "message": _("Order Pickup confirmed successfully."),
                "order_id": order_id,
                "delivery_note_id": delivery_note_id,
                "transporter": delivery_note.transporter,
                "custom_delivery_status": delivery_note.custom_delivery_status,
                "deliveryPersonLocation": liveLocation,
            },
            # user=get_user_by_order_id(order_id).name,
            room=order_id,
        )

        frappe.publish_realtime(
            "liveTrackingUpdates",
            {
                "status": "Confirmed by Rider",
                "message": _("Order Pickup confirmed by rider."),
                "order_id": order_id,
                "delivery_note_id": delivery_note_id,
                "transporter": delivery_note.transporter,
                "custom_delivery_status": delivery_note.custom_delivery_status,
                "deliveryPersonLocation": liveLocation,
            },
            # user=get_user_by_order_id(order_id).name,
            room=order_id,
        )

        # Prepare the response
        frappe.response["data"] = {
            "status": "success",
            "message": _("Order Pickup confirmed successfully."),
            "delivery_note_id": delivery_note_id,
            "transporter": delivery_note.transporter,
            "custom_delivery_status": delivery_note.custom_delivery_status
        }

    except frappe.PermissionError as e:
        # Handle permission errors
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}

    except frappe.ValidationError as e:
        # Handle validation errors
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {
            "message": "Validation error",
            "error": str(e),
        }

    except Exception as e:
        # Handle unexpected errors
        frappe.log_error(frappe.get_traceback(), "Confirm Order API Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True, methods=["POST"])
def updateOrderStatus(delivery_note_id, order_id, status, deliveryPersonLocation):
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Ensure the user is logged in and is a delivery partner
        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.", frappe.PermissionError)

        # Check if delivery_note_id is provided
        if not delivery_note_id:
            frappe.throw(_("Delivery Note ID is required"), frappe.ValidationError)
        
        if not deliveryPersonLocation:
            frappe.throw(_("Delivery person's location is required"), frappe.ValidationError)

        # Fetch the delivery note using the provided delivery_note_id
        delivery_note = frappe.get_doc("Delivery Note", delivery_note_id)

        # Ensure the delivery note exists and is assigned to the logged-in user
        if delivery_note.transporter != frappe.local.session.user:
            frappe.throw(_("Not your delivery"), frappe.ValidationError)

        # Update the custom delivery status
        delivery_note.custom_delivery_status = status
        delivery_note.save(ignore_permissions=True)  # Save without permissions check

        # Insert the delivery person's location
        insert_user_location(frappe.local.session.user, deliveryPersonLocation)

        # Submit the delivery note if status is 'Delivered'
        if status == 'Delivered':
            delivery_note.submit()

        # Publish live tracking updates
        frappe.publish_realtime(
            "liveTrackingUpdates",
            {
                "status": "success",
                "message": _("Order status updated successfully."),
                "order_id": order_id,
                "delivery_note_id": delivery_note_id,
                "transporter": delivery_note.transporter,
                "custom_delivery_status": delivery_note.custom_delivery_status,
                "deliveryPersonLocation": deliveryPersonLocation
            },
            room=order_id,
        )

        frappe.db.commit()  # Commit the changes

        # Prepare the response
        frappe.response["data"] = {
            "status": "success",
            "message": _("Order status updated successfully."),
            "delivery_note_id": delivery_note_id,
            "transporter": delivery_note.transporter,
            "custom_delivery_status": delivery_note.custom_delivery_status
        }

    except frappe.PermissionError as e:
        # Rollback in case of permission errors
        frappe.db.rollback()
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}

    except frappe.ValidationError as e:
        # Rollback in case of validation errors
        frappe.db.rollback()
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {
            "message": "Validation error",
            "error": str(e),
        }

    except Exception as e:
        # Rollback in case of unexpected errors
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Update Order Status API Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True, methods=["GET"])
def getOrders(status=None, deliveryPartner=None, page=1, page_size=10):
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
                fields=["name", "posting_date", "customer", "custom_delivery_status as status", "grand_total", "shipping_address_name"],
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
                if status == 'Delivered':
                    delivery_notes = frappe.get_all(
                        "Delivery Note",
                        # filters={"custom_delivery_status": status, "transporter": supplier.name, "docstatus":0},
                        filters={"custom_delivery_status": status, "transporter": deliveryPartner, "docstatus":1},
                        fields=["name", "posting_date", "customer", "custom_delivery_status as status", "grand_total", "shipping_address_name"],
                        limit_start=offset,
                        limit_page_length=limit
                    )
                elif status != '*':
                    delivery_notes = frappe.get_all(
                        "Delivery Note",
                        # filters={"custom_delivery_status": status, "transporter": supplier.name, "docstatus":0},
                        filters={"custom_delivery_status": status, "transporter": deliveryPartner, "docstatus":0},
                        fields=["name", "posting_date", "customer", "custom_delivery_status as status", "grand_total", "shipping_address_name"],
                        limit_start=offset,
                        limit_page_length=limit
                    )
                else:
                    delivery_notes = frappe.get_all(
                        "Delivery Note",
                        # filters={"custom_delivery_status": status, "transporter": supplier.name, "docstatus":0},
                        filters={
                            "custom_delivery_status": ["not in", "Delivered"],
                            # "custom_delivery_status": status, 
                            "transporter": deliveryPartner, "docstatus":0},
                        fields=["name", "posting_date", "customer", "custom_delivery_status as status", "grand_total", "shipping_address_name"],
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
            shipping_address_doc = frappe.get_doc("Address", note["shipping_address_name"])
            shipping_address_string = ", ".join(
                        [
                            shipping_address_doc.get("address_line1") or "",
                            shipping_address_doc.get("address_line2") or "",
                            shipping_address_doc.get("city") or "",
                            shipping_address_doc.get("state") or "",
                            shipping_address_doc.get("pincode") or "",
                            shipping_address_doc.get("country") or "",
                        ]
                    ).strip(", ")
            note["deliveryLocation"] = {
                "latitude": shipping_address_doc.custom_latitude,
                "longitude": shipping_address_doc.custom_longitude,
                "address": shipping_address_string
            }
            note["pickupLocation"] = {
                "latitude": 40.710859722407754,
                "longitude": -73.79381336441809,
                "address": "87-55 168 PL, Jamaica, NY, 11432, United States"
            }
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
                    "image": item.image
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


@frappe.whitelist(allow_guest=True, methods=["GET"])
def getOrderInfo(delivery_note_id):
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.", frappe.PermissionError)
        
        delivery_notes = []

        delivery_notes = frappe.get_all(
                "Delivery Note",
                filters={"name": delivery_note_id},
                fields=["name", "posting_date", "customer", "custom_delivery_status as status", "grand_total", "shipping_address_name"],
            )
        
        # Loop through each delivery note to fetch the linked sales order
        for note in delivery_notes:
            # Fetch the linked Sales Order using the 'against_sales_order' field from Delivery Note Item
            sales_order = frappe.db.get_value("Delivery Note Item", {"parent": note["name"]}, "against_sales_order")
            order_doc = frappe.get_doc("Sales Order", sales_order)
            shipping_address_doc = frappe.get_doc("Address", note["shipping_address_name"])
            shipping_address_string = ", ".join(
                        [
                            shipping_address_doc.get("address_line1", ""),
                            shipping_address_doc.get("address_line2", ""),
                            shipping_address_doc.get("city", ""),
                            shipping_address_doc.get("state", ""),
                            shipping_address_doc.get("pincode", ""),
                            shipping_address_doc.get("country", ""),
                        ]
                    ).strip(", ")
            note["deliveryLocation"] = {
                "latitude": shipping_address_doc.custom_latitude,
                "longitude": shipping_address_doc.custom_longitude,
                "address": shipping_address_string
            }
            note["pickupLocation"] = {
                "latitude": 40.710859722407754,
                "longitude": -73.79381336441809,
                "address": "87-55 168 PL, Jamaica, NY, 11432, United States"
            }
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
                    "image": item.image
                }
                for item in order_doc.items
            ])

        frappe.response["data"] = {
            "status": "success",
            "delivery_notes": delivery_notes
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


def insert_user_location(user, liveLocation):
    # Check if 'latitude' and 'longitude' fields exist in the 'liveLocation'
    if 'latitude' not in liveLocation or 'longitude' not in liveLocation:
        frappe.throw(_("Both 'latitude' and 'longitude' are required fields"), frappe.ValidationError)

    latitude = liveLocation['latitude']
    longitude = liveLocation['longitude']

    # Validate that latitude and longitude are numeric
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        frappe.throw(_("Latitude and Longitude must be numeric values"), frappe.ValidationError)

    # Check if latitude is within the valid range of -90 to 90
    if not (-90 <= latitude <= 90):
        frappe.throw(_("Latitude must be between -90 and 90"), frappe.ValidationError)

    # Check if longitude is within the valid range of -180 to 180
    if not (-180 <= longitude <= 180):
        frappe.throw(_("Longitude must be between -180 and 180"), frappe.ValidationError)

    # Create a new location entry
    location_doc = frappe.get_doc({
        "doctype": "User Location",
        "user": user,
        "latitude": latitude,
        "longitude": longitude,
        "ip_address": frappe.local.request.headers.get('X-Forwarded-For') or frappe.local.request.remote_addr,
        "location_timestamp": frappe.utils.now()
    })

    # Save the document to the database
    location_doc.save(ignore_permissions=True)

    # Return True if the location is successfully saved
    return True


def get_user_by_order_id(order_id):
    try:
        # Fetch the Sales Order document using the order_id
        sales_order = frappe.get_doc("Sales Order", order_id)

        if not sales_order:
            frappe.throw(_("Sales Order not found for the given order_id"), frappe.DoesNotExistError)

        # Get the customer linked to the Sales Order
        customer = sales_order.customer

        # Fetch the contact linked to the customer using Dynamic Link in Contact Doctype
        contact_name = frappe.db.get_value("Dynamic Link", {
            "link_doctype": "Customer",
            "link_name": customer,
            "parenttype": "Contact"
        }, "parent")

        if not contact_name:
            frappe.throw(_("No contact found for the customer linked to this Sales Order"), frappe.DoesNotExistError)

        # Retrieve the user's email address from the Contact
        user_email = frappe.db.get_value("Contact", contact_name, "email_id")

        if not user_email:
            frappe.throw(_("No user found for the customer linked to this Sales Order"), frappe.DoesNotExistError)

        # Fetch the user details using the email id
        user_details = frappe.get_doc("User", user_email)

        # Return the user details directly
        return user_details

    except frappe.DoesNotExistError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        return {
            "status": "error",
            "message": str(e)
        }

    except frappe.PermissionError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        return {
            "status": "error",
            "message": "Permission error",
            "error": str(e)
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error getting user by order_id")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        return {
            "status": "error",
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e)
        }
