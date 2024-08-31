import frappe
from frappe import _
frappe.utils.logger.set_log_level("DEBUG")
logger = frappe.logger("api", allow_site=True, file_count=50)

@frappe.whitelist(allow_guest=True)
def get_zone_by_zip(zip_code):
    try:
        if not zip_code:
            frappe.throw(_("Zip code is required"), frappe.exceptions.ValidationError)

        # Fetch the Zone Name where the zip code is found in the comma-separated list
        zone = frappe.db.sql("""
            SELECT 
                `zone_name` 
            FROM 
                `tabDelivery Zone` 
            WHERE 
                FIND_IN_SET(%s, `zip_codes`) > 0
            """, (zip_code), as_dict=True)
        
        if not zone:
            frappe.throw(_("No delivery zone found for the provided zip code: {0}").format(zip_code), frappe.exceptions.DoesNotExistError)
        
        return {"zone": zone[0].zone_name}

    except frappe.exceptions.ValidationError as e:
        frappe.log_error(message=str(e), title="Validation Error in get_zone_by_zip")
        return {"error": str(e)}

    except frappe.exceptions.DoesNotExistError as e:
        frappe.log_error(message=str(e), title="Zone Not Found")
        return {"error": str(e)}

    except Exception as e:
        frappe.log_error(message=str(e), title="Unexpected Error in get_zone_by_zip")
        return {"error": "An unexpected error occurred. Please try again later."}

@frappe.whitelist(allow_guest=True)
def signup_customer(full_name, email, mobile=None, password=None, confirm_password=None):
    try:
        # Check if passwords match
        if password != confirm_password:
            raise ValueError(_("Passwords do not match"))

        # Check if user already exists
        if frappe.db.exists("User", email):
            raise ValueError(_("User with this email {0}, already exists").format(email))

        # Check if mobile number is unique
        if mobile:
            if frappe.db.exists("Customer", {"mobile_no": mobile}):
                raise ValueError(_("A customer with this mobile number already exists"))

        # Start a database transaction
        frappe.db.begin()

        # Create the User
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "mobile_no": mobile,
            "first_name": full_name.split()[0],  # Assumes first name is the first part of full name
            "last_name": " ".join(full_name.split()[1:]),  # Assumes last name is the rest
            "enabled": 1,
            "user_type": "Website User",
            "roles": [
                {"role": "Customer"}  # Add Customer role to the user
            ],
            "new_password": password,  # Set the user's password
            "send_welcome_email": 0  # Avoid sending the welcome email automatically
        })

        user.insert(ignore_permissions=True)

        # Create the Customer
        customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": full_name,
            "customer_type": "Individual",
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
            "email_id": email,
            "mobile_no": mobile,
            "portal_users": [{"user": user.name}]  # Ensure we use `user.name` here
        })
        customer.insert(ignore_permissions=True)

        # Commit the transaction
        frappe.db.commit()

        # Optionally send a welcome email
        send_welcome_email(email, full_name)  # Commented out to skip welcome email

        return {"status": "success", "message": _("User and Customer created successfully")}

    except ValueError as ve:
        # frappe.log_error(message=str(ve), title="Signup Validation Error")
        frappe.db.rollback()
        return {"status": "error", "message": str(ve)}

    except Exception as e:
        frappe.log_error(message=str(e), title="Signup Customer Error")
        frappe.db.rollback()
        return {"status": "error", "message": _("An error occurred while creating the user and customer")}


def send_welcome_email(email, full_name):
    try:
        # Define the template path
        template_path = 'keno_store/templates/emails/welcome_email.html'
        
        # Attempt to get and render the template
        try:
            template = frappe.get_template(template_path)
        except IOError:
            error_message = _("Email template not found at path: {0}").format(template_path)
            frappe.log_error(message=error_message, title="Email Template Error")
            return {"status": "error", "message": error_message}
        
        # Render the email content with context
        message = template.render({
            'customer_name': full_name
        })
        
        # Define email subject
        subject = _("Welcome to Keno Store!")
        
        # Send the email
        frappe.sendmail(
            recipients=[email],
            subject=subject,
            message=message,
            delayed=False,
            retry=3
        )
        
        return {"status": "success", "message": _("Welcome email sent successfully to {0}").format(email)}
    
    except frappe.OutgoingEmailError as e:
        error_message = _("Failed to send welcome email to {0}. SMTP server error: {1}").format(email, str(e))
        frappe.log_error(message=error_message, title="SMTP Error")
        return {"status": "error", "message": error_message}
    
    except Exception as e:
        error_message = _("An unexpected error occurred while sending welcome email to {0}: {1}").format(email, str(e))
        frappe.log_error(message=error_message, title="Send Welcome Email Error")
        return {"status": "error", "message": error_message}


@frappe.whitelist(allow_guest=True)
def get_weekly_schedule_by_zip(zip_code):
    try:
        if not zip_code:
            frappe.throw(_("Zip code is required"), frappe.exceptions.ValidationError)

        # Find the Delivery Zone that contains the given zip code
        zone = frappe.get_all(
            "Delivery Zone",
            filters={"zip_codes": ["like", f"%{zip_code}%"]},
            fields=["zone_name"],
            limit_page_length=1  # Ensure only one zone is fetched
        )

        if not zone:
            frappe.throw(_("No zone found for the provided zip code: {0}").format(zip_code), frappe.exceptions.DoesNotExistError)

        zone_name = zone[0].zone_name

        # Fetch the Delivery Zone Schedule for the found zone
        schedule_doc = frappe.get_doc("Delivery Zone Schedule", {"delivery_zone": zone_name})

        # Initialize a dictionary to hold the weekly schedule
        weekly_schedule = {}

        # Weekdays in Frappe format with the corresponding field names for child tables
        weekdays = {
            "Monday": "monday_slots",
            "Tuesday": "tuesday_slots",
            "Wednesday": "wednesday_slots",
            "Thursday": "thursday_slots",
            "Friday": "friday_slots",
            "Saturday": "saturday_slots",
            "Sunday": "sunday_slots"
        }

        # Iterate through each weekday and fetch the delivery slots from the child table
        for day, field_name in weekdays.items():
            # Access the child table records from the parent document
            delivery_slots = [
                {"start_time": str(slot.start_time), "end_time": str(slot.end_time)}
                for slot in getattr(schedule_doc, field_name, [])
            ]

            # Only add the day to the schedule if there are slots available
            if delivery_slots:
                weekly_schedule[day] = delivery_slots

        return {"zip_code": zip_code, "zone": zone_name, "weekly_schedule": weekly_schedule}

    except frappe.exceptions.ValidationError as e:
        frappe.log_error(message=str(e), title="Validation Error in get_weekly_schedule_by_zip")
        return {"error": str(e)}

    except frappe.exceptions.DoesNotExistError as e:
        frappe.log_error(message=str(e), title="Zone Not Found")
        return {"error": str(e)}

    except Exception as e:
        frappe.log_error(message=str(e), title="Unexpected Error in get_weekly_schedule_by_zip")
        return {"error": "An unexpected error occurred. Please try again later."}