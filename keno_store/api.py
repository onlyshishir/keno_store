from datetime import datetime
import hashlib
from http import HTTPStatus
import json
import uuid
from bs4 import BeautifulSoup
import frappe
from frappe import _
from frappe.auth import CookieManager, validate_auth_via_api_keys
from frappe.contacts.doctype.contact.contact import get_contact_name
from frappe.email.doctype.email_template.email_template import get_email_template
from frappe.utils import cint, get_datetime
from frappe.utils import flt
import frappe.utils
from webshop.webshop.doctype.item_review.item_review import add_item_review
from webshop.webshop.product_data_engine.filters import ProductFiltersBuilder
from webshop.webshop.product_data_engine.query import ProductQuery
from webshop.webshop.doctype.override_doctype.item_group import get_child_groups_for_website
from webshop.webshop.utils.product import get_non_stock_item_status
from webshop.webshop.shopping_cart.product_info import get_product_info_for_website
from frappe.email.doctype.newsletter.newsletter import subscribe
from babel.dates import format_date
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
            # "roles": [
            #     {"role": "Customer"}  # Add Customer role to the user
            # ],
            "role_profile_name": "Customer",
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
        # send_welcome_email(email, full_name)  # Commented out to skip welcome email
        send_welcome_email_from_settings(email, full_name)  # Commented out to skip welcome email

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
    

def send_welcome_email_from_settings(email, full_name):
    try:
        template_name = frappe.db.get_system_setting("welcome_email_template")
        subject = "Welcome Email"
        args = {
            "full_name": full_name,
            "title": subject,
            "logo_url": 'https://keno.today/assets/logo.png',
            "created_by": "Administrator",
        }
        if template_name:
            email_template = get_email_template(template_name, args)
            subject = email_template.get("subject")
            content = email_template.get("message")

        frappe.sendmail(
            recipients=email,
            sender=None,
            subject=subject,
            template="welcome_email_template" if not email_template else None,
            content=content if email_template else None,
            args=args,
            delayed=False,
            retry=3,
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
def get_weekly_schedule(zip_code=None):
    try:
        # Initialize a dictionary to hold the schedule for all zones
        all_zone_schedules = {}

        # If a zip code is provided, find the specific zone
        if zip_code:
            zones = frappe.get_all(
                "Delivery Zone",
                filters={"zip_codes": ["like", f"%{zip_code}%"]},
                fields=["zone_name"],
                limit_page_length=1  # Ensure only one zone is fetched
            )

            if not zones:
                frappe.throw(_("No zone found for the provided zip code: {0}").format(zip_code), frappe.exceptions.DoesNotExistError)
        else:
            # If no zip code is provided, get all zones
            zones = frappe.get_all(
                "Delivery Zone",
                fields=["zone_name"]
            )

            if not zones:
                frappe.throw(_("No zones found in the system"), frappe.exceptions.DoesNotExistError)

        # Weekdays and corresponding field names for child tables
        weekdays = {
            "Monday": "monday_slots",
            "Tuesday": "tuesday_slots",
            "Wednesday": "wednesday_slots",
            "Thursday": "thursday_slots",
            "Friday": "friday_slots",
            "Saturday": "saturday_slots",
            "Sunday": "sunday_slots"
        }

        # Iterate over each zone and fetch the weekly schedule
        for zone in zones:
            zone_name = zone.zone_name

            # Fetch the Delivery Zone Schedule for the current zone
            try:
                schedule_doc = frappe.get_doc("Delivery Zone Schedule", {"delivery_zone": zone_name})

                # Initialize a dictionary to hold the weekly schedule for the current zone
                weekly_schedule = {}

                # Iterate through each weekday and fetch the delivery slots
                for day, field_name in weekdays.items():
                    delivery_slots = [
                        {"start_time": str(slot.start_time), "end_time": str(slot.end_time)}
                        for slot in getattr(schedule_doc, field_name, [])
                    ]

                    # Only add the day to the schedule if there are slots available
                    if delivery_slots:
                        weekly_schedule[day] = delivery_slots

                # Add the zone's weekly schedule to the overall schedules dictionary
                if weekly_schedule:
                    all_zone_schedules[zone_name] = {"weekly_schedule": weekly_schedule}

            except frappe.DoesNotExistError:
                frappe.log_error(message=f"Schedule not found for zone: {zone_name}", title="Schedule Not Found")
                continue

        # Return the appropriate result
        if zip_code:
            return {"zones": {zones[0].zone_name: all_zone_schedules.get(zones[0].zone_name)}}
        else:
            return {"zones": all_zone_schedules}

    except frappe.exceptions.ValidationError as e:
        frappe.log_error(message=str(e), title="Validation Error in get_weekly_schedule_by_zip")
        return {"error": str(e)}

    except frappe.exceptions.DoesNotExistError as e:
        frappe.log_error(message=str(e), title="Zone or Schedule Not Found")
        return {"error": str(e)}

    except Exception as e:
        frappe.log_error(message=str(e), title="Unexpected Error in get_weekly_schedule_by_zip")
        return {"error": "An unexpected error occurred. Please try again later."}



@frappe.whitelist(allow_guest=True)
def get_product_filter_data(query_args=None):
    """
    Returns filtered products and discount filters.

    Args:
        query_args (dict): contains filters to get products list
	   Query Args filters:
        search (str): Search Term.
        field_filters (dict): Keys include item_group, brand, etc.
        attribute_filters(dict): Keys include Color, Size, etc.
        start (int): Offset items by
        item_group (str): Valid Item Group
        from_filters (bool): Set as True to jump to page 1
    """
    if isinstance(query_args, str):
        query_args = json.loads(query_args)

    
    query_args = frappe._dict(query_args)

    if query_args:
        search = query_args.get("search")
        field_filters = query_args.get("field_filters", {})
        attribute_filters = query_args.get("attribute_filters", {})
        start = cint(query_args.start) if query_args.get("start") else 0
        item_group = query_args.get("item_group")
        from_filters = query_args.get("from_filters")
        logger.debug(field_filters)
    else:
        search, attribute_filters, item_group, from_filters = None, None, None, None
        field_filters = {}
        start = 0

    # if new filter is checked, reset start to show filtered items from page 1
    if from_filters:
        start = 0

    sub_categories = []
    if item_group:
        sub_categories = get_child_groups_for_website(item_group, immediate=True)

    engine = ProductQuery()

    try:
        result = engine.query(
            attribute_filters,
            field_filters,
            search_term=search,
            start=start,
            item_group=item_group,
        )
    except Exception:
        frappe.log_error("Product query with filter failed")
        return {"exc": "Something went wrong!"}

    # discount filter data
    filters = {}
    discounts = result["discounts"]

    if discounts:
        filter_engine = ProductFiltersBuilder()
        filters["discount_filters"] = filter_engine.get_discount_filters(discounts)
        logger.debug(filters["discount_filters"])
    
    # Adding ratings to each product
    for item in result["items"]:
        item_code = item.get("item_code")
        if item_code:
            ratings = frappe.db.get_all("Item Review", filters={"item": item_code}, fields=["rating"])
            if ratings:
                total_rating = sum([r["rating"] for r in ratings])
                average_rating = total_rating / len(ratings)
                item["rating"] = round(average_rating, 1)  # Round to 2 decimal places
            else:
                item["rating"] = 0  # Default if no ratings available


    return {
        "items": result["items"] or [],
        "filters": filters,
        "settings": engine.settings,
        "sub_categories": sub_categories,
        "items_count": result["items_count"],
    }


@frappe.whitelist(allow_guest=True)
def get_website_item_details(item_code):
    try:
        # Fetch the website item document
        website_item = frappe.get_doc("Website Item", {"item_code": item_code})
        logger.debug(website_item)

        if not website_item:
            frappe.throw(_("Website Item not found"))

        # Basic website item details
        item_details = {
            "item_code": website_item.item_code,
            "item_name": website_item.item_name,
            "description": website_item.short_description,
            # "image": website_item.website_image,
            "item_group": website_item.item_group,
            "web_long_description": website_item.web_long_description,
            "is_in_stock": frappe.db.get_value("Bin", {"item_code": item_code}, "actual_qty") > 0,
            "uom": website_item.stock_uom,
        }
        # get_stock_availability(item_details, website_item.get("website_warehouse"));
        

        # Get stock quantity
        stock_qty = frappe.db.get_value("Bin", {"item_code": item_code}, "projected_qty ")
        item_details["stock_qty"] = stock_qty if stock_qty else 0

        try:
            # Fetch product information including pricing details
            product_info = get_product_info_for_website(item_code, skip_quotation_creation=True).get(
                "product_info"
            )
            if product_info and product_info["price"]:
                item_details.update({
                    "currency": product_info["price"].get("currency"),
                    "formatted_mrp": product_info["price"].get("formatted_mrp"),
                    "formatted_price": product_info["price"].get("formatted_price"),
                    "price_list_rate": product_info["price"].get("price_list_rate")
                })
            if product_info["price"].get("discount_percent"):
                item_details.update({
                    "discount_percent" : flt(product_info["price"].discount_percent)
                })
            if product_info["price"].get("formatted_mrp"):
                item_details.update({
                    "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                        "formatted_discount_rate"
                    )
                })
        except Exception as e:
            frappe.log_error(message=f"Error fetching product info for item {item_code}: {str(e)}", 
                                title="Get New Website Items Error")
            # You may also choose to skip this item or return a default value instead
            

        # Get item price from Item Price doctype
        # item_price = frappe.db.get_value("Item Price", {"item_code": item_code, "selling": 1}, ["price_list_rate", "currency"], as_dict=True)
        # if item_price:
        #     item_details.update({
        #         "price": item_price.price_list_rate,
        #         "currency": item_price.currency
        #     })

        # Get item reviews from the custom Website Item Review doctype
        reviews = frappe.get_all("Item Review", filters={"item": item_code},
                                 fields=["customer", "rating", "review_title", "comment", "published_on"], order_by="published_on desc")

        item_details.update({
            "reviews": reviews,
            "average_rating": round(sum([r['rating'] for r in reviews]) / len(reviews),1) if reviews else 0
        })

        # Get multiple images from Website Slideshow Item
        logger.debug(website_item.slideshow)
        image_list = []
        slideshow_name = website_item.slideshow
        
        if slideshow_name:
            images = frappe.get_all("Website Slideshow Item", filters={"parent": slideshow_name}, fields=["image"], order_by="idx asc")
            image_list = [{"image": img.image} for img in images]
        else : 
            image_list = [{"image": website_item.website_image}]
        item_details["image_list"] = image_list

        # Fetch website specifications
        specifications = frappe.get_all("Item Website Specification", filters={"parent": website_item.name}, 
                                        fields=["label", "description"], order_by="idx asc")
        item_details["specifications"] = [{"label": spec.label, "value": extract_value(spec.description)} for spec in specifications]

        logger.debug(item_details)

        return item_details

    except frappe.DoesNotExistError:
        frappe.log_error(f"Website Item with code {item_code} does not exist.", "Item Not Found Error")
        return {"error": f"Website Item with code {item_code} does not exist."}, 404

    except frappe.ValidationError as e:
        frappe.log_error(str(e), "Validation Error")
        return {"error": str(e)}, 400

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Unexpected Error in get_website_item_details API")
        return {"error": "An unexpected error occurred. Please try again later."}, 500
    

def extract_value(content):
    # Check if content contains HTML tags
    if "<" in content and ">" in content:
        # Parse as HTML
        soup = BeautifulSoup(content, "html.parser")
        # Get value from the <p> tag, or return None if no <p> tag is found
        p_tag = soup.find("p")
        return p_tag.text if p_tag else None
    else:
        # Return the content as is if it's not HTML
        return content


def get_stock_availability(item):
    """Modify item object and add stock details."""
    from webshop.templates.pages.wishlist import (
        get_stock_availability as get_stock_availability_from_template,
	)
    # item.update({
    #     "is_stock_item": False
    # })

    item.is_in_stock = get_stock_availability_from_template(item.item_code, item.warehouse)
    # # warehouse = item_details.get("website_warehouse")
    # is_stock_item = frappe.get_cached_value("Item", item_details.item_code, "is_stock_item")
    # logger.debug(is_stock_item)

    # # if item_details.get("on_backorder"):
    # #     return

    # if not is_stock_item:
    #     if warehouse:
    #         # product bundle case
    #         item_details.is_in_stock = get_non_stock_item_status(item_details.item_code, "website_warehouse")
    #     else:
    #         item_details.is_in_stock = True
    # elif warehouse:
    #     # stock item and has warehouse
    #     item_details.is_in_stock = get_stock_availability_from_template(item_details.item_code, warehouse)

# @frappe.whitelist(allow_guest=True)
# def search(query=None):
#     from webshop.templates.pages.product_search import (
#         product_search as product_search_from_template,
#         get_category_suggestions as get_category_suggestions_from_template
#     )
#     product_results = product_search_from_template(query)
#     category_results = get_category_suggestions_from_template(query)
    
#     return {
# 		"product_results": product_results.get("results") or [],
# 		"category_results": category_results.get("results") or [],
# 	}

@frappe.whitelist(allow_guest=True, methods=["GET"])
def search(query=None, page=1, page_size=10):
    try:
        # Validate Authorization header
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)
        
        # Validate API key authorization
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw("Authorization header is malformed or missing API keys.", frappe.AuthenticationError)

        validate_auth_via_api_keys(api_keys)
        
        # Validate and parse page and page_size
        try:
            page = int(page)
            page_size = int(page_size)
            if page <= 0 or page_size <= 0:
                raise ValueError("Page and page size must be positive integers")
        except ValueError as e:
            frappe.throw(_("Invalid page or page size: {0}").format(str(e)), frappe.InvalidRequestError)

        # Calculate offset and limit for pagination
        offset = (page - 1) * page_size
        limit = page_size
        items = frappe.get_all(
            "Item",
            filters={
                "item_name": ["like", f"%{query}%"],
                "disabled": 0,                # Assuming this field indicates if the item is active
                "published_in_website": 1              # Assuming this field indicates if the item is published
            },
            fields=["item_code"],
            limit_start=offset,
            limit_page_length=limit
        )

        item_codes = [item["item_code"] for item in items]

        # Fetch website items corresponding to the top-selling item codes
        searched_items = frappe.get_all(
            "Website Item",
            filters={"item_code": ["in", item_codes], "published": 1},
            fields=[
                "web_item_name", 
                "name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "short_description", 
                "ranking",
            ]
        )

        for item in searched_items:
            try:
                # Get stock quantity
                stock_qty = frappe.db.get_value("Bin", {"item_code": item.item_code}, "projected_qty")
                item["stock_qty"] = stock_qty if stock_qty else 0

                # Get product pricing information
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get("product_info")
                if product_info and product_info["price"]:
                    item.update({
                        "currency": product_info["price"].get("currency"),
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                    if product_info["price"].get("discount_percent"):
                        item.update({
                            "discount_percent": flt(product_info["price"].get("discount_percent")),
                            "discount": product_info["price"].get("formatted_discount_percent") or product_info["price"].get("formatted_discount_rate")
                        })
            except Exception as e:
                frappe.log_error(f"Error fetching product info for item {item.name}: {str(e)}", "Get Offer Items API")

            try:
                # Get item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0  # No ratings, default to 0
            except Exception as e:
                frappe.log_error(f"Error fetching ratings for item {item.item_code}: {str(e)}", "Get Offer Items API")
                item["rating"] = 0  # Default rating if fetching fails

        # Determine total items and total pages for pagination
        total_items = frappe.db.count(
            "Item",
            filters={
                "item_name": ["like", f"%{query}%"],
                "disabled": 0,
                "published_in_website": 1 
            }
        )

        total_pages = (total_items + page_size - 1) // page_size  # Ceiling division

        # Return the response with pagination details
        frappe.response["data"] = {
            "status": "success",
            "items": searched_items,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": total_pages,
            },
        }
    
    except frappe.AuthenticationError:
        frappe.local.response["http_status_code"] = 401
        frappe.response["data"] = {
            "status": "error",
            "message": "Unauthorized access. Invalid or missing API key."
        }
    except frappe.ValidationError as e:
        frappe.local.response["http_status_code"] = 400
        frappe.response["data"] = {
            "status": "error",
            "message": str(e)
        }
    except Exception as e:
        frappe.log_error(f"An unexpected error occurred: {str(e)}", "Get Top Selling Product API")
        frappe.local.response["http_status_code"] = 500
        frappe.response["data"] = {
            "status": "error",
            "message": "An unexpected error occurred. Please try again later."
        }


@frappe.whitelist(allow_guest=True)
def get_new_website_items(limit=10, price_list="Standard Selling"):
    """
    Fetch the latest website items based on creation date.

    Args:
        limit (int): The number of items to return (default is 10).

    Returns:
        dict: A dictionary containing the list of new website items.
    """
    try:
        # Fetch the latest items from the Website Item doctype
        items = frappe.get_all(
            "Website Item",
            filters={"published": 1},  # Ensure only published items are fetched
            fields=[
                "web_item_name", 
                "name", 
                "item_name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "web_long_description", 
                "short_description", 
                "route", 
                "website_warehouse", 
                "ranking", 
                "on_backorder"
            ],
            order_by="creation desc",  # Order by creation date to get the newest items
            limit=limit  # Limit the number of items returned
        )
        
        for item in items:
            try:
                # Fetch product information including pricing details
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get(
                    "product_info"
                )
                if product_info and product_info["price"]:
                    item.update({
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                if product_info["price"].get("discount_percent"):
                    item.update({
                        "discount_percent" : flt(product_info["price"].discount_percent)
                    })
                if item.formatted_mrp:
                    item.update({
                        "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                            "formatted_discount_rate"
                        )
                    })
            except Exception as e:
                frappe.log_error(message=f"Error fetching product info for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                # You may also choose to skip this item or return a default value instead
                continue

            try:
                # Fetch item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0
            except Exception as e:
                frappe.log_error(message=f"Error fetching ratings for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                item["rating"] = 0  # Default value if rating fetch fails

    except Exception as e:
        frappe.log_error(message=f"Error fetching website items: {str(e)}", title="Get New Website Items Error")
        return {"error": "An error occurred while fetching new website items."}

    return {"items": items}


@frappe.whitelist(allow_guest=True)
def get_hot_deals_website_items(limit=10, price_list="Standard Selling"):
    """
    Fetch website items that have active pricing rules ("Hot Deals").

    Args:
        limit (int): The number of items to return (default is 10).
        price_list (str): The price list to fetch item prices from (default is "Standard Selling").

    Returns:
        dict: A dictionary containing the list of hot deal website items or an error message.
    """
    try:
        # Fetch active pricing rules
        active_pricing_rules = frappe.get_all(
            "Pricing Rule",
            filters={
                "disable": 0,
                "apply_on": "Item Code",
                "valid_from": ["<=", frappe.utils.nowdate()],
                "valid_upto": ["in", ["", None, ["gt", frappe.utils.nowdate()]]],
                "discount_percentage": [">", 0]
            },
            fields=["name"]
        )

        # If no active pricing rules are found, return an empty list
        if not active_pricing_rules:
            return {"items": []}

        # Fetch items linked to these pricing rules
        pricing_rule_names = [rule["name"] for rule in active_pricing_rules]
        items = frappe.get_all(
            "Pricing Rule Item Code",
            filters={"parent": ["in", pricing_rule_names]},
            fields=["item_code"]
        )
        logger.debug(items)

        item_codes = [item["item_code"] for item in items]

        # Step 2: Fetch Website Items linked to these item codes
        items = frappe.get_all(
            "Website Item",
            filters={
                "item_code": ["in", item_codes],
                "published": 1  # Ensure only published items are fetched
            },
            fields=[
                "web_item_name", 
                "name", 
                "item_name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "web_long_description", 
                "short_description", 
                "route", 
                "website_warehouse", 
                "ranking", 
                "on_backorder"
            ],
            order_by="creation desc",  # Order by creation date to get the newest items
            limit=limit
        )

        # Step 3: Enhance each item with pricing and rating details
        for item in items:
            try:
                # Fetch product information including pricing details
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get(
                    "product_info"
                )
                if product_info and product_info["price"]:
                    item.update({
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                if product_info["price"].get("discount_percent"):
                    item.update({
                        "discount_percent" : flt(product_info["price"].discount_percent)
                    })
                if item.formatted_mrp:
                    item.update({
                        "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                            "formatted_discount_rate"
                        )
                    })
            except Exception as e:
                frappe.log_error(message=f"Error fetching product info for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                # You may also choose to skip this item or return a default value instead
                continue

            try:
                # Fetch item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0
            except Exception as e:
                frappe.log_error(message=f"Error fetching ratings for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                item["rating"] = 0  # Default value if rating fetch fails

        return {"items": items}

    except Exception as e:
        frappe.log_error(f"Failed to get hot deals: {str(e)}")
        return {"exc": "Something went wrong!"}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_top_selling_products(period="last_month", page=1, page_size=10):
    try:
        # Validate Authorization header
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)
        
        # Validate API key authorization
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw("Authorization header is malformed or missing API keys.", frappe.AuthenticationError)

        validate_auth_via_api_keys(api_keys)
        
        # Validate and parse page and page_size
        try:
            page = int(page)
            page_size = int(page_size)
            if page <= 0 or page_size <= 0:
                raise ValueError("Page and page size must be positive integers")
        except ValueError as e:
            frappe.throw(_("Invalid page or page size: {0}").format(str(e)), frappe.InvalidRequestError)

        # Calculate offset and limit for pagination
        offset = (page - 1) * page_size
        limit = page_size
        # Define the period for fetching sales data
        if period == "last_month":
            start_date = frappe.utils.add_months(frappe.utils.nowdate(), -1)
        elif period == "last_week":
            start_date = frappe.utils.add_days(frappe.utils.nowdate(), -7)
        else:
            start_date = None  # Use all available data if no specific period is defined

        filters = {}
        if start_date:
            filters["creation"] = [">=", start_date]

        # Fetch top-selling items based on quantity sold
        top_items = frappe.db.get_all(
            "Sales Invoice Item",
            filters=filters,
            fields=["item_code", "sum(qty) as total_sold"],
            group_by="item_code",
            order_by="total_sold desc",
            limit_start=offset,
            limit_page_length=limit
        )

        item_codes = [item["item_code"] for item in top_items]

        # Fetch website items corresponding to the top-selling item codes
        top_selling_items = frappe.get_all(
            "Website Item",
            filters={"item_code": ["in", item_codes], "published": 1},
            fields=[
                "web_item_name", 
                "name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "short_description", 
                "ranking",
            ]
        )

        for item in top_selling_items:
            try:
                # Get stock quantity
                stock_qty = frappe.db.get_value("Bin", {"item_code": item.item_code}, "projected_qty")
                item["stock_qty"] = stock_qty if stock_qty else 0

                # Get product pricing information
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get("product_info")
                if product_info and product_info["price"]:
                    item.update({
                        "currency": product_info["price"].get("currency"),
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                    if product_info["price"].get("discount_percent"):
                        item.update({
                            "discount_percent": flt(product_info["price"].get("discount_percent")),
                            "discount": product_info["price"].get("formatted_discount_percent") or product_info["price"].get("formatted_discount_rate")
                        })
            except Exception as e:
                frappe.log_error(f"Error fetching product info for item {item.name}: {str(e)}", "Get Offer Items API")

            try:
                # Get item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0  # No ratings, default to 0
            except Exception as e:
                frappe.log_error(f"Error fetching ratings for item {item.item_code}: {str(e)}", "Get Offer Items API")
                item["rating"] = 0  # Default rating if fetching fails

        # Determine total items and total pages for pagination
        total_items = frappe.db.get_all(
            "Sales Invoice Item",
            filters=filters,
            fields=["item_code", "sum(qty) as total_sold"],
            group_by="item_code",
            order_by="total_sold desc")
        total_pages = (len(total_items) + page_size - 1) // page_size  # Ceiling division

        # Return the response with pagination details
        frappe.response["data"] = {
            "status": "success",
            "items": top_selling_items,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_items": len(total_items),
                "total_pages": total_pages,
            },
        }
    
    except frappe.AuthenticationError:
        frappe.local.response["http_status_code"] = 401
        frappe.response["data"] = {
            "status": "error",
            "message": "Unauthorized access. Invalid or missing API key."
        }
    except frappe.ValidationError as e:
        frappe.local.response["http_status_code"] = 400
        frappe.response["data"] = {
            "status": "error",
            "message": str(e)
        }
    except Exception as e:
        frappe.log_error(f"An unexpected error occurred: {str(e)}", "Get Top Selling Product API")
        frappe.local.response["http_status_code"] = 500
        frappe.response["data"] = {
            "status": "error",
            "message": "An unexpected error occurred. Please try again later."
        }


@frappe.whitelist(allow_guest=True)
def get_limited_time_offers(page=1, page_size=10, price_list="Standard Selling"):
    """
    Fetch hot deals (items with active pricing rules) that will expire within the next X days.
    Args:
        limit (int): The number of items to return (default is 10).
        price_list (str): The price list to fetch item prices from (default is "Standard Selling").
    Returns:
        dict: A dictionary containing the list of hot deal website items or an error message.
    """
    try:
        # Validate Authorization header
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)
        
        # Validate API key authorization
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw("Authorization header is malformed or missing API keys.", frappe.AuthenticationError)

        validate_auth_via_api_keys(api_keys)
        
        # Validate and parse page and page_size
        try:
            page = int(page)
            page_size = int(page_size)
            if page <= 0 or page_size <= 0:
                raise ValueError("Page and page size must be positive integers")
        except ValueError as e:
            frappe.throw(_("Invalid page or page size: {0}").format(str(e)), frappe.InvalidRequestError)

        # Calculate offset and limit for pagination
        offset = (page - 1) * page_size
        limit = page_size

        # Fetch active pricing rules
        # active_pricing_rules = frappe.get_all(
        #     "Pricing Rule",
        #     filters={
        #         "disable": 0,
        #         "apply_on": "Item Code",
        #         "valid_from": ["<=", today_date],
        #         "valid_upto": ["between", [today_date, expiring_soon_date]],
        #         "discount_percentage": [">", 0]
        #     },
        #     fields=["name", "valid_upto"]
        # )
        promotional_scheme_doc = frappe.get_doc("Promotional Scheme", "Limited Time Offer")

        limited_time_offer_rules = frappe.get_all(
            "Pricing Rule",
            filters={
                "disable": 0,
                "promotional_scheme": "Limited Time Offer",
                "valid_from": ["<=", frappe.utils.nowdate()],
                "discount_percentage": [">", 0]
            },
            fields=["name", "valid_upto", "apply_on"]
        )

        # If no active pricing rules are found, return an empty list
        if not limited_time_offer_rules:
            return {"items": []}

        # Map pricing rule valid_upto to item codes
        pricing_rule_map = {rule["name"]: rule["valid_upto"] for rule in limited_time_offer_rules}
        pricing_rule_names = list(pricing_rule_map.keys())

        if(limited_time_offer_rules[0].apply_on == 'Item Code'):
            # Fetch items linked to these pricing rules
            items = frappe.get_all(
                "Pricing Rule Item Code",
                filters={"parent": ["in", pricing_rule_names]},
                fields=["item_code", "parent"]
            )
        elif (limited_time_offer_rules[0].apply_on == 'Item Group'):
            item_groups = frappe.get_all(
                "Pricing Rule Item Group",
                filters={
                    "parent": limited_time_offer_rules[0].name
                },
                fields=["item_group"]
            )
            item_group_names = [item_group["item_group"] for item_group in item_groups]
            # Fetch items linked to these item_groups
            items = frappe.get_all(
                "Item",
                filters={"item_group": ["in", item_group_names], "published_in_website":1},
                fields=["item_code"]
            )

        if not items:
            return {"items": []}

        item_codes = [item["item_code"] for item in items]

        # Step 2: Fetch Website Items linked to these item codes
        website_items = frappe.get_all(
            "Website Item",
            filters={
                "item_code": ["in", item_codes],
                "published": 1  # Ensure only published items are fetched
            },
            fields=[
                "web_item_name", 
                "name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "short_description", 
                "ranking",
            ],
            order_by="ranking desc",
            limit_start=offset,
            limit_page_length=limit
        )

        # Create a dictionary to map item codes to their associated pricing rule expiry dates
        # expiry_dates = {item["item_code"]: pricing_rule_map.get(item.get("parent")) for item in items}
        expiry_dates = limited_time_offer_rules[0].valid_upto

        # Step 3: Enhance each item with pricing, rating, and valid_upto details
        for item in website_items:
            try:
                # Attach the expiry date from the pricing rule to the item if available
                # item["offer_ends"] = "This offer ends on "+ date_to_words(expiry_dates.get(item["item_code"]))
                item["offer_ends"] = "This offer ends on "+ date_to_words(expiry_dates)
                # Get stock quantity
                stock_qty = frappe.db.get_value("Bin", {"item_code": item.item_code}, "projected_qty")
                item["stock_qty"] = stock_qty if stock_qty else 0

                # Get product pricing information
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get("product_info")
                if product_info and product_info["price"]:
                    item.update({
                        "currency": product_info["price"].get("currency"),
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                    if product_info["price"].get("discount_percent"):
                        item.update({
                            "discount_percent": flt(product_info["price"].get("discount_percent")),
                            "discount": product_info["price"].get("formatted_discount_percent") or product_info["price"].get("formatted_discount_rate")
                        })

                # Add pricing rule expiration date
                # item["pricing_rule_expiration"] = pricing_rule_map.get(item.get("parent"))
                # item["pricing_rule_expiration"] = pricing_rule_map.get('PRLE-0003')

            except Exception as e:
                frappe.log_error(f"Error fetching product info for item {item.name}: {str(e)}", "Get Offer Items API")

            try:
                # Get item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0  # No ratings, default to 0
            except Exception as e:
                frappe.log_error(f"Error fetching ratings for item {item.item_code}: {str(e)}", "Get Offer Items API")
                item["rating"] = 0  # Default rating if fetching fails

        # Determine total items and total pages for pagination
        total_items = frappe.db.count(
            "Website Item",
            filters={
                "item_code": ["in", item_codes],
                "published": 1  # Ensure only published items are fetched
            },
            )
        total_pages = (total_items + page_size - 1) // page_size  # Ceiling division

        # Return the response with pagination details
        frappe.response["data"] = {
            "status": "success",
            "valid_upto": end_of_day_iso(promotional_scheme_doc.valid_upto),
            "items": website_items,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": total_pages,
            },
        }

    except frappe.AuthenticationError:
        frappe.local.response["http_status_code"] = 401
        frappe.response["data"] = {
            "status": "error",
            "message": "Unauthorized access. Invalid or missing API key."
        }
    except frappe.ValidationError as e:
        frappe.local.response["http_status_code"] = 400
        frappe.response["data"] = {
            "status": "error",
            "message": str(e)
        }
    except Exception as e:
        frappe.log_error(f"An unexpected error occurred: {str(e)}", "Get Limited Time Offers API")
        frappe.local.response["http_status_code"] = 500
        frappe.response["data"] = {
            "status": "error",
            "message": "An unexpected error occurred. Please try again later."
        }


def end_of_day_iso(date_string):
    # Combine date string with '23:59:59'
    date_with_time = f"{date_string} 23:59:59"
    
    # Convert to datetime using Frappe's get_datetime utility
    datetime_obj = get_datetime(date_with_time)
    
    # Convert to ISO format
    iso_datetime = datetime_obj.isoformat()
    
    return iso_datetime


def date_to_words(date_str):
    """
    Converts a date string from Frappe to a human-readable date in words.
    
    Args:
        date_str (str): The date string in 'YYYY-MM-DD' format.

    Returns:
        str: The date in words, e.g., "October 21, 2024."
    """
    # Ensure the date string is in the correct format
    try:
        date_obj = frappe.utils.getdate(date_str)
        # Format the date to words using Babel's format_date
        date_in_words = format_date(date_obj, format="long", locale="en")
        return date_in_words
    except Exception as e:
        frappe.log_error(f"Error converting date: {e}", "Date to Words Conversion")
        return None
    

@frappe.whitelist(allow_guest=True)
def get_special_discount_items(limit=10):
    """
    Fetch special discount (items with active offer named "special discount").

    Args:
        limit (int): The number of items to return (default is 10).

    Returns:
        dict: A dictionary containing the list of special discount website items or an error message.
    """
    # Fetch Website Offers with the offer_title "Special Discount"
    try:
        # Fetch web item codes by "Special Discount" offer title
        parents = frappe.get_all(
            "Website Offer",
            filters={"offer_title": "Special Discount"},
            fields=["parent"],
            limit=limit
        )
        if not parents:
            return {"items": []}
        
        web_items = ','.join([parent['parent'] for parent in parents])

        # Fetch Website Items linked to these web_items
        items = frappe.get_all(
            "Website Item",
            filters={
                "name": ["in", web_items],
                "published": 1  # Ensure only published items are fetched
            },
            fields=[
                "web_item_name", 
                "name", 
                "item_name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "web_long_description", 
                "short_description", 
                "route", 
                "website_warehouse", 
                "ranking", 
                "on_backorder"
            ],
            order_by="creation desc",  # Order by creation date to get the newest items
            limit=limit
        )

        logger.debug("get_special_discount_items")
        logger.debug(items)

        # Step 3: Enhance each item with pricing and rating details
        for item in items:
            try:
                # Fetch product information including pricing details
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get(
                    "product_info"
                )
                if product_info and product_info["price"]:
                    item.update({
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                if product_info["price"].get("discount_percent"):
                    item.update({
                        "discount_percent" : flt(product_info["price"].discount_percent)
                    })
                if item.formatted_mrp:
                    item.update({
                        "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                            "formatted_discount_rate"
                        )
                    })
            except Exception as e:
                frappe.log_error(message=f"Error fetching product info for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                # You may also choose to skip this item or return a default value instead
                continue

            try:
                # Fetch item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0
            except Exception as e:
                frappe.log_error(message=f"Error fetching ratings for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                item["rating"] = 0  # Default value if rating fetch fails

        return {"items": items}

    except Exception as e:
        frappe.log_error(f"Failed to get hot deals: {str(e)}")
        return {"exc": "Something went wrong!"}
    

@frappe.whitelist(allow_guest=True)
def get_offer_items(offer_title, page=1, page_size=10):
    try:
        # Validate Authorization header
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)
        
        # Validate API key authorization
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw("Authorization header is malformed or missing API keys.", frappe.AuthenticationError)

        validate_auth_via_api_keys(api_keys)
        
        # Validate and parse page and page_size
        try:
            page = int(page)
            page_size = int(page_size)
            if page <= 0 or page_size <= 0:
                raise ValueError("Page and page size must be positive integers")
        except ValueError as e:
            frappe.throw(_("Invalid page or page size: {0}").format(str(e)), frappe.InvalidRequestError)

        # Calculate offset and limit for pagination
        offset = (page - 1) * page_size
        limit = page_size

        promotional_scheme_doc = frappe.get_doc("Promotional Scheme", offer_title)

        offer_rules = frappe.get_all(
            "Pricing Rule",
            filters={
                "disable": 0,
                "promotional_scheme": promotional_scheme_doc.name,
                "valid_from": ["<=", frappe.utils.nowdate()],
                "discount_percentage": [">", 0]
            },
            fields=["name", "valid_upto", "apply_on"]
        )

        # If no active pricing rules are found, return an empty list
        if not offer_rules:
            return {"items": []}

        # Map pricing rule valid_upto to item codes
        pricing_rule_map = {rule["name"]: rule["valid_upto"] for rule in offer_rules}
        pricing_rule_names = list(pricing_rule_map.keys())

        if(offer_rules[0].apply_on == 'Item Code'):
            # Fetch items linked to these pricing rules
            items = frappe.get_all(
                "Pricing Rule Item Code",
                filters={"parent": ["in", pricing_rule_names]},
                fields=["item_code", "parent"]
            )
        elif (offer_rules[0].apply_on == 'Item Group'):
            item_groups = frappe.get_all(
                "Pricing Rule Item Group",
                filters={
                    "parent": offer_rules[0].name
                },
                fields=["item_group"]
            )
            item_group_names = [item_group["item_group"] for item_group in item_groups]
            # Fetch items linked to these item_groups
            items = frappe.get_all(
                "Item",
                filters={"item_group": ["in", item_group_names], "published_in_website":1},
                fields=["item_code"]
            )

        if not items:
            return {"items": []}

        item_codes = [item["item_code"] for item in items]

        # Step 2: Fetch Website Items linked to these item codes
        website_items = frappe.get_all(
            "Website Item",
            filters={
                "item_code": ["in", item_codes],
                "published": 1  # Ensure only published items are fetched
            },
            fields=[
                "web_item_name", 
                "name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "short_description", 
                "ranking",
            ],
            order_by="ranking desc",
            limit_start=offset,
            limit_page_length=limit
        )

        # Create a dictionary to map item codes to their associated pricing rule expiry dates
        # expiry_dates = {item["item_code"]: pricing_rule_map.get(item.get("parent")) for item in items}
        expiry_dates = offer_rules[0].valid_upto

        # Step 3: Enhance each item with pricing, rating, and valid_upto details
        for item in website_items:
            try:
                # Attach the expiry date from the pricing rule to the item if available
                # item["offer_ends"] = "This offer ends on "+ date_to_words(expiry_dates.get(item["item_code"]))
                item["offer_ends"] = "This offer ends on "+ date_to_words(expiry_dates)
                # Get stock quantity
                stock_qty = frappe.db.get_value("Bin", {"item_code": item.item_code}, "projected_qty")
                item["stock_qty"] = stock_qty if stock_qty else 0

                # Get product pricing information
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get("product_info")
                if product_info and product_info["price"]:
                    item.update({
                        "currency": product_info["price"].get("currency"),
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                    if product_info["price"].get("discount_percent"):
                        item.update({
                            "discount_percent": flt(product_info["price"].get("discount_percent")),
                            "discount": product_info["price"].get("formatted_discount_percent") or product_info["price"].get("formatted_discount_rate")
                        })

                # Add pricing rule expiration date
                # item["pricing_rule_expiration"] = pricing_rule_map.get(item.get("parent"))
                # item["pricing_rule_expiration"] = pricing_rule_map.get('PRLE-0003')

            except Exception as e:
                frappe.log_error(f"Error fetching product info for item {item.name}: {str(e)}", "Get Offer Items API")

            try:
                # Get item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0  # No ratings, default to 0
            except Exception as e:
                frappe.log_error(f"Error fetching ratings for item {item.item_code}: {str(e)}", "Get Offer Items API")
                item["rating"] = 0  # Default rating if fetching fails

        # Determine total items and total pages for pagination
        total_items = frappe.db.count(
            "Website Item",
            filters={
                "item_code": ["in", item_codes],
                "published": 1  # Ensure only published items are fetched
            },
            )
        total_pages = (total_items + page_size - 1) // page_size  # Ceiling division

        # Return the response with pagination details
        frappe.response["data"] = {
            "status": "success",
            "valid_upto": end_of_day_iso(promotional_scheme_doc.valid_upto),
            "items": website_items,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": total_pages,
            },
        }

    except frappe.AuthenticationError:
        frappe.local.response["http_status_code"] = 401
        frappe.response["data"] = {
            "status": "error",
            "message": "Unauthorized access. Invalid or missing API key."
        }
    except frappe.ValidationError as e:
        frappe.local.response["http_status_code"] = 400
        frappe.response["data"] = {
            "status": "error",
            "message": str(e)
        }
    except Exception as e:
        frappe.log_error(f"An unexpected error occurred: {str(e)}", "Get Limited Time Offers API")
        frappe.local.response["http_status_code"] = 500
        frappe.response["data"] = {
            "status": "error",
            "message": "An unexpected error occurred. Please try again later."
        }
    

@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_offer_items_old(offer_title, page=1, page_size=10):
    try:
        # Validate Authorization header
        auth_header = frappe.get_request_header("Authorization", str)
        if not auth_header:
            frappe.throw("Missing Authorization header.", frappe.AuthenticationError)
        
        # Validate API key authorization
        api_keys = auth_header.split(" ")[1:]
        if not api_keys:
            frappe.throw("Authorization header is malformed or missing API keys.", frappe.AuthenticationError)

        validate_auth_via_api_keys(api_keys)

        # Validate offer title
        if not offer_title:
            frappe.throw("Offer Title is mandatory", frappe.ValidationError)
        
        # Validate and parse page and page_size
        try:
            page = int(page)
            page_size = int(page_size)
            if page <= 0 or page_size <= 0:
                raise ValueError("Page and page size must be positive integers")
        except ValueError as e:
            frappe.throw(_("Invalid page or page size: {0}").format(str(e)), frappe.InvalidRequestError)

        # Calculate offset and limit for pagination
        offset = (page - 1) * page_size
        limit = page_size

        # Fetch web item codes by offer title
        parents = frappe.get_all(
            "Website Offer",
            filters={"offer_title": offer_title},
            fields=["parent"]
        )
        if not parents:
            frappe.response["data"] = {
                "message": "No items found for the specified offer.",
                "items": []
            }
            return
        
        # Extract web item codes
        web_items = [parent['parent'] for parent in parents]
        
        if not web_items:
            frappe.response["data"] = {
                "message": "No website items linked to this offer.",
                "items": []
            }
            return

        # Fetch Website Items linked to the web_items
        items = frappe.get_all(
            "Website Item",
            filters={
                "name": ["in", web_items],
                "published": 1  # Ensure only published items are fetched
            },
            fields=[
                "web_item_name", 
                "name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "short_description", 
                "ranking", 
            ],
            order_by="ranking desc",
            limit_start=offset,
            limit_page_length=limit
        )

        # Enhance items with stock, pricing, and rating information
        for item in items:
            try:
                # Get stock quantity
                stock_qty = frappe.db.get_value("Bin", {"item_code": item.item_code}, "projected_qty")
                item["stock_qty"] = stock_qty if stock_qty else 0

                # Get product pricing information
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get("product_info")
                if product_info and product_info["price"]:
                    item.update({
                        "currency": product_info["price"].get("currency"),
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                    if product_info["price"].get("discount_percent"):
                        item.update({
                            "discount_percent": flt(product_info["price"].get("discount_percent")),
                            "discount": product_info["price"].get("formatted_discount_percent") or product_info["price"].get("formatted_discount_rate")
                        })
            except Exception as e:
                frappe.log_error(f"Error fetching product info for item {item.name}: {str(e)}", "Get Offer Items API")

            try:
                # Get item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0  # No ratings, default to 0
            except Exception as e:
                frappe.log_error(f"Error fetching ratings for item {item.item_code}: {str(e)}", "Get Offer Items API")
                item["rating"] = 0  # Default rating if fetching fails

        # Determine total items and total pages for pagination
        total_items = len(web_items)
        total_pages = (total_items + page_size - 1) // page_size  # Ceiling division

        # Return the response with pagination details
        frappe.response["data"] = {
            "status": "success",
            "items": items,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": total_pages,
            },
        }

    except frappe.AuthenticationError:
        frappe.local.response["http_status_code"] = 401
        frappe.response["data"] = {
            "status": "error",
            "message": "Unauthorized access. Invalid or missing API key."
        }
    except frappe.ValidationError as e:
        frappe.local.response["http_status_code"] = 400
        frappe.response["data"] = {
            "status": "error",
            "message": str(e)
        }
    except Exception as e:
        frappe.log_error(f"An unexpected error occurred: {str(e)}", "Get Offer Items API")
        frappe.local.response["http_status_code"] = 500
        frappe.response["data"] = {
            "status": "error",
            "message": "An unexpected error occurred. Please try again later."
        }


@frappe.whitelist(allow_guest=True)
def get_items_by_pricing_rule(pricing_rule_name=None, limit=None):
    """Fetch items based on a Pricing Rule that applies to item_code, item_group, or brand."""
    if not pricing_rule_name:
        frappe.throw(("Pricing Rule name is required"))

    try:
        limit = int(limit) if limit else None

        # Fetch Pricing Rule details
        pricing_rules = frappe.get_all("Pricing Rule", filters={"title": pricing_rule_name, "disable": 0})
        if not pricing_rules:
            frappe.throw(("Pricing Rule not found"))

        pricing_rule = frappe.get_doc("Pricing Rule", pricing_rules[0].name)

        filters = {}
        items = []

        # Determine rule application
        if pricing_rule.apply_on == "Item Code":
            # Fetch items associated with the Pricing Rule directly
            items = frappe.get_all("Pricing Rule Item Code", filters={"parent": pricing_rule.name}, fields=["item_code"])

        elif pricing_rule.apply_on == "Item Group":
            # Fetch items in the specified item group
            filters["item_group"] = pricing_rule.item_group

        elif pricing_rule.apply_on == "Brand":
            # Fetch items in the specified brand
            filters["brand"] = pricing_rule.brand

        else:
            frappe.throw(_("Unsupported rule_based_on value"))

        # If applicable, fetch items based on item group or brand
        if filters:
            items = frappe.get_all("Item", filters=filters, fields=["item_code"])

        item_codes = [item["item_code"] for item in items]

        # Step 2: Fetch Website Items linked to these item codes
        website_items = frappe.get_all(
            "Website Item",
            filters={
                "item_code": ["in", item_codes],
                "published": 1  # Ensure only published items are fetched
            },
            fields=[
                "web_item_name", 
                "name", 
                "item_name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "web_long_description", 
                "short_description", 
                "route", 
                "website_warehouse", 
                "ranking", 
                "on_backorder"
            ],
            order_by="creation desc",  # Order by creation date to get the newest items
            limit=limit
        )

        # Step 3: Enhance each item with pricing, rating, and valid_upto details
        for item in website_items:
            try:
                # Fetch product information including pricing details
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get(
                    "product_info"
                )
                if product_info and product_info["price"]:
                    item.update({
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })

                if product_info["price"].get("discount_percent"):
                    item.update({
                        "discount_percent": flt(product_info["price"].get("discount_percent"))
                    })

                if item.get("formatted_mrp"):
                    item.update({
                        "discount": product_info["price"].get("formatted_discount_percent") or 
                                    product_info["price"].get("formatted_discount_rate")
                    })

                # Map valid_upto from pricing rule to the item
                pricing_rule_entry = next((p for p in items if p["item_code"] == item["item_code"]), None)
                if pricing_rule_entry:
                    item["valid_upto"] = pricing_rule.valid_upto

            except Exception as e:
                frappe.log_error(message=f"Error fetching product info for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                continue

            try:
                # Fetch item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0
            except Exception as e:
                frappe.log_error(message=f"Error fetching ratings for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                item["rating"] = 0  # Default value if rating fetch fails

        frappe.response["data"] = {"items": website_items}

    except frappe.DoesNotExistError:
        frappe.response["data"] = {"message": "Failed to fetch items", "error": str(e)}

    except frappe.ValidationError as e:
        frappe.log_error(("Validation error: {0}".format(str(e))))
        frappe.response["data"] = {"message": "Validation error occurred", "error": str(e)}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Unexpected Error in get_items_by_pricing_rule")
        frappe.response["data"] = {"message": "An unexpected error occurred. Please try again later.", "error": str(e)}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def submit_item_review(item_code, review):
    """
    Submits a review for an item.

    Args:
        item_code (str): The code of the item being reviewed.
        review (dict): The review details containing rating, review_title, and comment.
    """
    try:
        validate_auth_via_api_keys(frappe.get_request_header("Authorization", str).split(" ")[1:])
        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.") 
        
         # Get the current user
        user = frappe.local.session.user

        # Ensure that rating is within the allowed range (1 to 5 stars)
        rating = review.get("rating")
        if rating < 0 or rating > 5:
            frappe.throw(_("Rating must be between 0 and 5"), frappe.ValidationError)

        # Check if the item exists
        if not frappe.db.exists("Item", item_code):
            frappe.throw(_("Item does not exist"), frappe.DoesNotExistError)

        # Check if the item is listed on the website
        website_item = frappe.db.get_value("Website Item", {"item_code": item_code}, "name")
        if not website_item:
            frappe.throw(_("This item is not listed as a website item."), frappe.DoesNotExistError)

        # Get the current customer (reviewer)
        customer = frappe.db.get_value("Customer", {"email_id": frappe.session.user}, "name")
        if not customer:
            frappe.throw(_("You must be a registered customer to submit a review."), frappe.ValidationError)

        # add_item_review(website_item, review.get("review_title"), round(float(rating), 2), review.get("comment"))

        if not frappe.db.exists("Item Review", {"user": frappe.session.user, "website_item": website_item}):
            # Create a new Item Review document
            item_review = frappe.get_doc({
                "doctype": "Item Review",
                "item": item_code,
                "website_item": website_item,
                "rating": rating,
                "review_title": review.get("review_title"),
                "comment": review.get("comment"),
                "user": user,
                "customer": get_customer(),  # Set the customer who submitted the review
                "published_on": datetime.today().strftime("%d %B %Y")  # Set the current datetime for published_on
            })

            # Save the review
            item_review.save()
            frappe.db.set_value("Item Review", item_review.name, "rating", rating)
            frappe.db.commit()
        else:
            frappe.throw(_("You have existing review"), frappe.ValidationError)

        frappe.response["data"] = {"message": _("Review submitted successfully")}

    except frappe.DoesNotExistError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.NOT_FOUND
        frappe.response["data"] = {"message": "Item not found", "error": str(e)}

    except frappe.ValidationError as e:
        frappe.local.response["http_status_code"] = HTTPStatus.BAD_REQUEST
        frappe.response["data"] = {"message": "Validation error", "error": str(e)}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Write Item Review API Error")
        frappe.local.response["http_status_code"] = HTTPStatus.INTERNAL_SERVER_ERROR
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later.",
            "error": str(e),
        }


@frappe.whitelist(allow_guest=True)
def add_to_wishlist(item_code):
    try:
        validate_auth_via_api_keys(frappe.get_request_header("Authorization", str).split(" ")[1:])
        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.") 
        # Check if item already exists in the wishlist
        if frappe.db.exists("Wishlist Item", {"item_code": item_code, "parent": frappe.session.user}):
            return {"message": "Item already in wishlist"}
        
        # Fetch Web Item Data by item_code
        web_item_data = frappe.db.get_value(
            "Website Item",
            {"item_code": item_code},
            [
                "website_image",
                "website_warehouse",
                "name",
                "web_item_name",
                "item_name",
                "item_group",
                "route",
            ],
            as_dict=1,
        )

        # Prepare the item to be added
        wished_item_dict = {
            "item_code": item_code,
            "item_name": web_item_data.get("item_name"),
            "item_group": web_item_data.get("item_group"),
            "website_item": web_item_data.get("name"),
            "web_item_name": web_item_data.get("web_item_name"),
            "image": web_item_data.get("website_image"),
            "warehouse": web_item_data.get("website_warehouse"),
            "route": web_item_data.get("route"),
        }

        # Add the item to the wishlist
        if not frappe.db.exists("Wishlist", frappe.session.user):
            # initialise wishlist
            wishlist = frappe.get_doc({"doctype": "Wishlist"})
            wishlist.user = frappe.session.user
            wishlist.append("items", wished_item_dict)
            wishlist.save(ignore_permissions=True)
        else:
            wishlist = frappe.get_doc("Wishlist", frappe.session.user)
            item = wishlist.append("items", wished_item_dict)
            item.db_insert()

        # Commit the transaction to the database
        frappe.db.commit()
        
        if hasattr(frappe.local, "cookie_manager"):
            frappe.local.cookie_manager.set_cookie("wish_count", str(len(wishlist.items)))

        frappe.response["data"] = {"message": "Item added to wishlist successfully"}

    except frappe.ValidationError as e:
        frappe.log_error(f"Error adding item to wishlist: {e}")
        frappe.response["data"] = {"message": "Failed to add item to wishlist", "error": str(e)}

    except Exception as e:
        frappe.log_error(f"Unexpected error: {e}")
        frappe.response["data"] = {"message": "An unexpected error occurred", "error": str(e)}
    

@frappe.whitelist(allow_guest=True)
def remove_from_wishlist(item_code):
    try:
        validate_auth_via_api_keys(frappe.get_request_header("Authorization", str).split(" ")[1:])
        if frappe.local.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.") 
        # Check if the item exists in the user's wishlist
        if frappe.db.exists("Wishlist Item", {"item_code": item_code, "parent": frappe.session.user}):
            # Delete the wishlist item
            frappe.db.delete("Wishlist Item", {"item_code": item_code, "parent": frappe.session.user})
            frappe.db.commit()  # Ensure the transaction is committed

            # Fetch updated wishlist items count for the user
            wishlist_items = frappe.db.get_values("Wishlist Item", filters={"parent": frappe.session.user}, fieldname="name")

            # Update the wish count in cookies
            if hasattr(frappe.local, "cookie_manager"):
                frappe.local.cookie_manager.set_cookie("wish_count", str(len(wishlist_items)))

            frappe.response["data"] = {"message": "Item removed from wishlist", "wish_count": len(wishlist_items)}
        else:
            frappe.response["data"] = {"message": "Item not found in wishlist", "wish_count": 0}

    except frappe.DoesNotExistError:
        frappe.log_error(f"Wishlist Item with item_code {item_code} not found for user {frappe.session.user}")
        frappe.response["data"] = {"error": "Item does not exist in the wishlist"}

    except frappe.ValidationError as e:
        frappe.log_error(f"Validation error while removing item from wishlist: {e}")
        frappe.response["data"] = {"message": "There was a validation error", "error": str(e)}

    except Exception as e:
        frappe.log_error(f"Unexpected error while removing item from wishlist: {str(e)}")
        frappe.response["data"] = {"message": "An unexpected error occurred", "error": str(e)}


@frappe.whitelist(allow_guest=True)
def get_wishlist():
    try:
        validate_auth_via_api_keys(frappe.get_request_header("Authorization", str).split(" ")[1:])
        # validate_auth_via_api_keys(frappe.get_request_header("Authorization", str))
        if frappe.session.user == None or frappe.session.user == "Guest":
            frappe.throw("Please log in to access this feature.")
        # Fetch all wishlist items for the current user
        wishlist_items = frappe.get_all(
            "Wishlist Item",
            filters={"parent": frappe.session.user},
            fields=["item_code", "item_name", "description", "image", "warehouse"]
        )

        # Loop through wishlist items and append price
        for item in wishlist_items:
            get_stock_availability(item)
            product_info = get_product_info_for_website(item["item_code"], skip_quotation_creation=True).get("product_info")
            if product_info and product_info["price"]:
                item.update({
                    "formatted_mrp": product_info["price"].get("formatted_mrp"),
                    "formatted_price": product_info["price"].get("formatted_price"),
                    "price_list_rate": product_info["price"].get("price_list_rate")
                })
            if product_info["price"].get("discount_percent"):
                item.update({
                    "discount_percent" : flt(product_info["price"].discount_percent)
                })
            if item.formatted_mrp:
                item.update({
                    "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                        "formatted_discount_rate"
                    )
                })

        frappe.response["data"] = {
                    "message": "Wishlist items fetched successfully.",
                    "wishlist_items": wishlist_items
                }
    
    except frappe.ValidationError as e:
        frappe.log_error(f"Validation error while retrieving wishlist for user: {e}")
        frappe.response["data"] = {"message": "There was a validation error", "error": str(e)}

    except Exception as e:
        frappe.log_error(f"Error retrieving wishlist for user {frappe.session.user}: {str(e)}")
        frappe.response["data"] = {"message": "An error occurred while retrieving the wishlist", "error": str(e)}
    

@frappe.whitelist(allow_guest=True)
def get_item_groups(limit=None):
    """
    API to fetch all active Item Groups in ERPNext, or limit the number of results.
    
    Args:
        limit (int, optional): The number of item groups to return. Defaults to None for all.
        
    Returns:
        dict: A dictionary containing the list of item groups.
    """
    try:
        # Set default limit to None to fetch all if no limit is specified
        limit = int(limit) if limit else None

        # Fetch active Item Groups with optional limit
        item_groups = frappe.get_all(
            "Item Group",
            filters={"show_in_website": 1},  # Filter only active groups shown on website
            fields=["name", "parent_item_group", "image", "is_group"],
            order_by="weightage desc",
            limit=limit  # Use limit only if provided, otherwise fetch all
        )

        if not item_groups:
            frappe.response["data"] = {
                "message": "No item groups found.",
                "item_groups": []
            }
        else:
            frappe.response["data"] = {
                "message": "Item groups fetched successfully.",
                "item_groups": item_groups
            }

    except Exception as e:
        frappe.log_error(f"Error fetching item groups: {str(e)}")
        frappe.response["data"] = {
            "error": "An error occurred while fetching item groups."
        }

@frappe.whitelist(allow_guest=True)
def get_child_item_groups_by_parent(parent_item_group, limit=None):
    """
    API to fetch all active Item Groups in ERPNext, or limit the number of results.
    
    Args:
        limit (int, optional): The number of item groups to return. Defaults to None for all.
        
    Returns:
        dict: A dictionary containing the list of item groups.
    """
    try:
        # Set default limit to None to fetch all if no limit is specified
        limit = int(limit) if limit else None

        # Fetch active Item Groups with optional limit
        item_groups = frappe.get_all(
            "Item Group",
            filters={"show_in_website": 1, "parent_item_group": parent_item_group},  # Filter only active groups shown on website
            fields=["name", "parent_item_group", "image", "is_group"],
            order_by="weightage desc",
            limit=limit  # Use limit only if provided, otherwise fetch all
        )

        if not item_groups:
            frappe.response["data"] = {
                "message": "No item groups found.",
                "item_groups": []
            }
        else:
            frappe.response["data"] = {
                "message": "Item groups fetched successfully.",
                "item_groups": item_groups
            }

    except Exception as e:
        frappe.log_error(f"Error fetching item groups: {str(e)}")
        frappe.response["data"] = {
            "error": "An error occurred while fetching item groups."
        }

def get_customer(silent=False):
	"""
	silent: Return customer if exists else return nothing. Dont throw error.
	"""
	user = frappe.session.user
	contact_name = get_contact_name(user)
	customer = None

	if contact_name:
		contact = frappe.get_doc("Contact", contact_name)
		for link in contact.links:
			if link.link_doctype == "Customer":
				customer = link.link_name
				break

	if customer:
		return frappe.db.get_value("Customer", customer)
	elif silent:
		return None
	else:
		# should not reach here unless via an API
		frappe.throw(
			_("You are not a verified customer yet. Please contact us to proceed."), exc=UnverifiedReviewer
		)


@frappe.whitelist(allow_guest=True)
def generate_session_id():
    # Get client IP address
    client_ip = frappe.get_request_header('X-Forwarded-For') or frappe.get_request_header('X-Real-IP') or frappe.local.request.remote_addr
    
    # Get user agent (browser and device info)
    user_agent = frappe.get_request_header('User-Agent')
    
    # Combine IP address and user-agent into a unique string
    unique_string = f"{client_ip}-{user_agent}-{uuid.uuid4()}"
    
    # Hash the unique string to generate session ID
    session_id = hashlib.sha256(unique_string.encode()).hexdigest()

    frappe.local.cookie_manager = CookieManager()
    frappe.local.cookie_manager.set_cookie('session_id', session_id, max_age=365 * 24 * 60 * 60, httponly=True, secure=False)
    
    # # Set session_id in response header as a cookie
    # frappe.set_cookie('session_id', session_id, max_age=365 * 24 * 60 * 60, httponly=True, secure=False)
    
    # Return the session ID as a response as well, if needed for logging
    return {
        'session_id': session_id,
        'ip_address': client_ip,
        'user_agent': user_agent
    }


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_coverage_area_info():
    try:
        # Pickup Locations
        pickup_locations = [
            {
                "address": "87-55 168 PL, Jamaica, NY 11432",
                "hours": "11AM - 1:30AM (Night)",
                "days": "7 Days a week"
            },
            {
                "address": "255-12 Hillside Ave, Queens, NY 11004",
                "hours": "10AM - 6:00PM",
                "days": "Monday-Thursday"
            },
            {
                "address": "105-07 150th ST Jamaica, NY 11435",
                "hours": "10AM - 6:00PM",
                "days": "Monday-Saturday"
            }
        ]

        # Home Delivery Info
        home_delivery = {
            "same_day_delivery": [
                {
                    "range": "$500 and up",
                    "delivery_charge": "Free",
                    "areas": ["Brooklyn", "Queens", "Long Island (Nassau County)"]
                },
                {
                    "range": "$200-$499",
                    "delivery_charge": "$10",
                    "areas": ["Brooklyn", "Queens", "Long Island (Nassau County)"]
                },
                {
                    "range": "Below $200",
                    "delivery_charge": "$15",
                    "areas": ["Brooklyn", "Queens", "Long Island (Nassau County)"]
                }
            ],
            "next_day_delivery": [
                {
                    "range": "$500 and up",
                    "delivery_charge": "Free",
                    "areas": ["Brooklyn", "Queens", "Long Island (Nassau County)"]
                },
                {
                    "range": "$200 and up",
                    "delivery_charge": "$5",
                    "areas": ["Brooklyn", "Queens", "Long Island (Nassau County)"]
                },
                {
                    "range": "Below $200",
                    "delivery_charge": "$10",
                    "areas": ["Brooklyn", "Queens", "Long Island (Nassau County)"]
                }
            ]
        }

        # Return API Response
        frappe.response["data"] = {
            "status": "success",
            "pickup_locations": pickup_locations,
            "home_delivery": home_delivery
        }
    
    except Exception as e:
        # Log error for debugging
        frappe.log_error(frappe.get_traceback(), "Failed to retrieve delivery info")
        frappe.throw(_("An error occurred while fetching delivery information."))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def subscribe_to_newsletter(email):
    try:
        newsletter_name = "Keno Newsletter"
        subscribe(email=email, email_group=newsletter_name)
        return {"status": "success", "message": "Successfully subscribed"}
    except Exception as e:
        frappe.log_error(f"Subscription Error: {str(e)}", "Newsletter Subscription Error")
        return {"status": "error", "message": "Subscription failed", "error": str(e)}
    

@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_slideshow(slideshow_name):
    try:
        # Check if slideshow exists
        slideshow = frappe.get_doc("Website Slideshow", slideshow_name)
        if not slideshow:
            frappe.throw(_("Slideshow not found"), frappe.DoesNotExistError)

        # Prepare slideshow data
        slideshow_data = {
            "title": slideshow.slideshow_name,
            "slides": []
        }

        for slide in slideshow.slideshow_items:
            slideshow_data["slides"].append({
                "image": slide.image,
                "caption": slide.heading,
                "description": slide.description,
                "url": slide.url
            })

        return slideshow_data
        # frappe.response["data"] = {
        #     "message": "Item groups fetched successfully.",
        #     "item_groups": item_groups
        # }

    except frappe.DoesNotExistError:
        frappe.local.response["http_status_code"] = 404
        frappe.response["data"] = {
            "message": "Slideshow not found"
        }

    except frappe.PermissionError:
        frappe.local.response["http_status_code"] = 403
        frappe.response["data"] = {
            "message": "Permission denied"
        }

    except Exception as e:
        frappe.log_error(f"Unexpected error in get_slideshow: {str(e)}", "Slideshow API Error")
        frappe.local.response["http_status_code"] = 500
        frappe.response["data"] = {
            "message": "An unexpected error occurred. Please try again later."
        }


@frappe.whitelist(allow_guest=True)
def download_app():
    user_agent = frappe.request.headers.get('User-Agent')

    if 'Android' in user_agent:
        # Redirect to Google Play Store
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "https://play.google.com/store/apps/details?id=com.amazon.mShop.android.shopping"
    elif 'iPhone' in user_agent or 'iPad' in user_agent:
        # Redirect to Apple App Store
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "https://apps.apple.com/us/app/amazon-shopping/id297606951"
    else:
        # Fallback URL (e.g., your website or app info page)
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "https://keno.today"
