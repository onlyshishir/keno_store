from http import HTTPStatus
import frappe
from frappe import _
from frappe.auth import validate_auth_via_api_keys


@frappe.whitelist(allow_guest=True, methods=["POST"])
def insert_user_location(liveLocation, address=None):
    """
    API to insert a user's location.

    Args:
        latitude (str): Latitude of the user's location.
        longitude (str): Longitude of the user's location.
        ip_address (str, optional): IP address of the user.
        address (str, optional): Address of the location.

    Returns:
        dict: Status of the operation.
    """
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.", frappe.PermissionError)

        # Get the current user
        user = frappe.local.session.user

        if user is None or user == "Guest":
            frappe.throw(
                "You need to be logged in to insert location.", frappe.PermissionError
            )

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
            "latitude": liveLocation.get("latitude"),
            "longitude": liveLocation.get("longitude"),
            "ip_address": frappe.local.request.headers.get('X-Forwarded-For') or frappe.local.request.remote_addr,
            "address": address,
            "location_timestamp": frappe.utils.now()
        })

        # Save the document to the database
        location_doc.save(ignore_permissions=True)

        frappe.response["data"] = {
            "status": "success",
            "message": _("User location has been added successfully.")
        }

    except frappe.PermissionError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}
    except Exception as e:
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {
            "message": "Exception occured",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_own_location_history(page=1, page_size=10):
    """
    API to fetch the location history of a user.

    Returns:
        dict: List of user's location history.
    """
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.", frappe.PermissionError)

        # Get the current user
        user = frappe.local.session.user

        if user is None or user == "Guest":
            frappe.throw(
                "You need to be logged in to insert location.", frappe.PermissionError
            )
        
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

        # Fetch the location history for own self
        location_histories = frappe.get_all(
            "User Location",
            filters={"user": user},
            fields=["latitude", "longitude", "ip_address", "address", "location_timestamp"],
            order_by="location_timestamp DESC",
            limit_start=offset,
            limit_page_length=limit
        )

        total_count = frappe.db.count(
            "User Location", filters={"user": user}
        )
        total_pages = (total_count + page_size - 1) // page_size
    
        frappe.response["data"] = {
            "status": "success",
            "location_history": location_histories,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_orders": total_count,
                "total_pages": total_pages,
            }
        }

    except frappe.PermissionError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}
    except Exception as e:
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {
            "message": "Exception occured",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_user_current_location(user_id):
    """
    API to fetch the current location of a user_id.

    Args:
        user (str): Username or email of the user.

    Returns:
        dict: User's current location.
    """
    try:
         # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.", frappe.PermissionError)

        # Get the current user
        user = frappe.local.session.user

        if user is None or user == "Guest":
            frappe.throw(
                "You need to be logged in to insert location.", frappe.PermissionError
            )

        # Validate if the user exists
        if not frappe.db.exists("User", user_id):
            frappe.throw(_("User does not exist"),frappe.DoesNotExistError)

        # Fetch the most recent location entry for the specified user
        current_location = frappe.get_all(
            "User Location",
            filters={"user": user_id},
            fields=["latitude", "longitude", "ip_address", "address", "location_timestamp"],
            order_by="location_timestamp DESC",
            limit=1
        )

        frappe.response["data"] = {
            "status": "success",
            "current_location": current_location[0] if current_location else {}
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error fetching user's current location")
        return {
            "status": "error",
            "message": str(e)
        }
    except frappe.PermissionError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.FORBIDDEN
        frappe.response["data"] = {"message": "Permission error", "error": str(e)}
    except frappe.PermissionError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        frappe.response["data"] = {"message": "Data not found error", "error": str(e)}
    except Exception as e:
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {
            "message": "Exception occured",
            "error": str(e),
        }
