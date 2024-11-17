import re
import frappe
from frappe.auth import LoginManager, validate_auth_via_api_keys
from frappe.core.doctype.user.user import User, update_password
from frappe.rate_limiter import rate_limit
from frappe.utils.password import get_password_reset_limit


@frappe.whitelist(allow_guest=True)
def custom_login(usr, pwd):
    login_manager = LoginManager()
    login_manager.authenticate(usr, pwd)
    login_manager.post_login()
    if frappe.response["message"] == "Logged In":
        user = login_manager.user
        frappe.response["sid"] = frappe.session.sid
        frappe.response["token"] = generate_token(user)
        frappe.response["user_details"] = get_user_details(user)
    else:
        return False


def generate_token(user):
    user_details = frappe.get_doc("User", user)
    api_secret = api_key = ""
    if not user_details.api_key and not user_details.api_secret:
        api_secret = frappe.generate_hash(length=15)
        api_key = frappe.generate_hash(length=15)
        user_details.api_key = api_key
        user_details.api_secret = api_secret
        user_details.save(ignore_permissions=True)
    else:
        api_secret = user_details.get_password("api_secret")
        api_key = user_details.get("api_key")
    # return {"api_secret": api_secret,"api_key": api_key}
    return "token " + api_key + ":" + api_secret


def get_user_details(user):
    user_details = frappe.get_all(
        "User",
        filters={"name": user},
        fields=[
            "name",
            "first_name",
            "last_name",
            "email",
            "mobile_no",
            "gender",
            "role_profile_name",
            "user_image",
        ],
    )

    if user_details and user_details[0].get("role_profile_name") == 'Customer':
        user_email = user_details[0].get("email")

        # Fetch the contact based on the email
        contact_name = frappe.get_all("Contact Email", filters={"email_id": user_email}, fields=["parent"], limit=1)
        customer = None

        # If a contact is found, retrieve the linked customer
        if contact_name:
            contact = frappe.get_doc("Contact", contact_name[0].parent)  # Access the 'parent' field
            for link in contact.links:
                if link.link_doctype == "Customer":
                    customer = link.link_name
                    break

        # If a customer is found, retrieve the primary address
        if customer:
            # address = frappe.db.get_value("Customer", customer, "primary_address")
            address = frappe.db.get_value("Customer", customer, "customer_primary_address")
            if address:
                address_doc = frappe.get_doc("Address", address)
                address = {
                    "address_line1": address_doc.address_line1,
                    "address_line2": address_doc.address_line2,
                    "city": address_doc.city,
                    "state": address_doc.state,
                    "pincode": address_doc.pincode,
                    "country": address_doc.country
                }
                user_details[0]["address"] = address;  # Add address to the user details

    if user_details:
        return user_details  # Return the first element (user details with address if applicable)
    else:
        return None


@frappe.whitelist(True)
def get_user_info():
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(
            frappe.get_request_header("Authorization", str).split(" ")[1:]
        )

        # Get the current user
        user = frappe.local.session.user

        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw(
                "Please log in to access this feature.", frappe.PermissionError
            )
        # # Get Authorization header
        # auth_header = frappe.get_request_header("Authorization", str).split(" ")

        # if len(auth_header) != 3 or auth_header[1].lower() != "token":
        #     return {"status": "error", "message": ("Invalid Authorization header")}

        # # Extract api_key and api_secret
        # api_key, api_secret = auth_header[2].split(":")

        # # Validate API key and secret
        # user = frappe.db.get_value("User", {"api_key": api_key}, "name")
        # if not user:
        #     return {"status": "error", "message": ("Invalid API Key")}

        # # Check if API secret matches
        # api_secret_stored = frappe.db.get_value(
        #     "User", {"api_key": api_key}, "api_secret"
        # )
        # if frappe.utils.password.check_password(api_secret_stored, api_secret):
        #     user_info = frappe.get_doc("User", user).as_dict()

        #     # Remove sensitive fields from the response
        #     user_info.pop("api_key", None)
        #     user_info.pop("api_secret", None)
        #     return {"status": "success", "user": user_info}

        # return {"status": "error", "message": ("Invalid API Secret")}
        frappe.response["sid"] = frappe.session.sid
        frappe.response["token"] = generate_token(user)
        frappe.response["user_details"] = get_user_details(user)

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_user_info")
        return {"status": "error", "message": {str(e)}}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=get_password_reset_limit, seconds=60 * 60)
def reset_password(user: str) -> str:
    try:
        user: User = frappe.get_doc("User", user)
        if user.name == "Administrator":
            frappe.throw("Not allowed.")
        if not user.enabled:
            frappe.throw("User disabled.")

        user.validate_reset_password()
        reset_pasword_link = user.reset_password(send_email=False)
        # reset_pasword_link = re.sub(
        #     r"^(http://)[^/]+", r"\1" + "kenotoday.vercel.app", reset_pasword_link
        # )
        # reset_pasword_link = reset_pasword_link.replace("https://erp.keno.today", "https://kenotoday.vercel.app")
        email_template = None

        template_name = frappe.db.get_system_setting("reset_password_template")
        subject = "Password Reset"
        args = {
            "first_name": user.first_name or user.last_name or "user",
            "user": user.name,
            "title": subject,
            "link": reset_pasword_link,
            "created_by": "Administrator",
        }
        if template_name:
            email_template = frappe.get_doc("Email Template", template_name)
            if email_template:
                email_template.get_formatted_email(args)
                subject = email_template.get("subject")
                content = email_template.get("message")

        frappe.sendmail(
            recipients=frappe.db.get_value("User", user, "email"),
            sender=None,
            subject=subject,
            template="password_reset" if not email_template else None,
            content=content if email_template else None,
            args=args,
            header=[subject, "green"],
            delayed=False,
            retry=3,
        )

        frappe.response["data"] = {
            "message": "Password reset instructions have been sent to your email",
            "link": {reset_pasword_link},
        }
    except frappe.ValidationError as e:
        # frappe.local.response["http_status_code"] = 404
        frappe.response["data"] = {
            "message": "There was a validation error",
            "error": str(e),
        }
    except frappe.DoesNotExistError:
        frappe.local.response["http_status_code"] = 404
        frappe.response["data"] = {"message": "Not found"}
    except Exception as e:
        frappe.local.response["http_status_code"] = 404
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True, methods=["POST"])
def change_own_password(current_password, new_password):
    """API for changing the password of the logged-in user"""
    try:
        # Validate API key authorization
        validate_auth_via_api_keys(frappe.get_request_header("Authorization", str).split(" ")[1:])

        # Get the current user
        user = frappe.session.user
        
        # Ensure the user is not a guest
        if user is None or user == "Guest":
            frappe.throw("Guests cannot change passwords", frappe.PermissionError)

        # Validate the current password
        if not frappe.local.login_manager.check_password(user, current_password):
            frappe.throw("Current password is incorrect", frappe.ValidationError)

        # Update the new password using the Frappe method
        update_password(new_password, 1, None, current_password)

        # Clear session cache for the user
        frappe.local.login_manager.logout(user=user)

        frappe.response["data"] =  {
            "status": "success",
            "message": "Password has been updated successfully. Please log in again."
        }
    except frappe.ValidationError as e:
        # Handle specific validation errors
        frappe.local.response["http_status_code"] = 403
        frappe.response["data"] = {"message": "There was a validation error", "error": str(e)}

    except frappe.AuthenticationError as e:
        frappe.local.response["http_status_code"] = 401
        frappe.response["data"] =  {
            "status": "fail",
            "message": "Authentication error: {0}".format(str(e))
        }
    except frappe.PermissionError as e:
        frappe.local.response["http_status_code"] = 404
        frappe.response["data"] =  {
            "status": "fail",
            "message": "Permission denied: {0}".format(str(e))
        }
    except Exception as e:
        frappe.local.response["http_status_code"] = 500
        frappe.response["data"] =  {
            "status": "fail",
            "message": "An error occurred: {0}".format(str(e))
        }