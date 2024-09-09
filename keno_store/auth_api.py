import frappe
from frappe.auth import LoginManager

@frappe.whitelist(allow_guest = True)
def custom_login(usr,pwd):
	login_manager = LoginManager()
	login_manager.authenticate(usr,pwd)
	login_manager.post_login()
	if frappe.response['message'] == 'Logged In':
		user = login_manager.user
		frappe.response['sid'] = frappe.session.sid
		frappe.response['token'] = generate_token(user)
		frappe.response['user_details'] = get_user_details(user)
	else:
		return False
	
def generate_token(user):
	user_details = frappe.get_doc("User", user)
	api_secret = api_key = ''
	if not user_details.api_key and not user_details.api_secret:
		api_secret = frappe.generate_hash(length=15)
		api_key = frappe.generate_hash(length=15)
		user_details.api_key = api_key
		user_details.api_secret = api_secret
		user_details.save(ignore_permissions = True)
	else:
		api_secret = user_details.get_password('api_secret')
		api_key = user_details.get('api_key')
	# return {"api_secret": api_secret,"api_key": api_key}
	return "token "+ api_key + ":" + api_secret

def get_user_details(user):
	user_details = frappe.get_all("User",filters={"name":user},fields=["name","first_name","last_name","email","mobile_no","gender","role_profile_name","user_image"])
	if user_details:
		return user_details
	

@frappe.whitelist(True)
def get_user_info():
    try:
        # Get Authorization header
        auth_header = frappe.get_request_header("Authorization", str).split(" ")

        if len(auth_header) != 3 or auth_header[1].lower() != 'token':
            return {"status": "error", "message": ("Invalid Authorization header")}

        # Extract api_key and api_secret
        api_key, api_secret = auth_header[2].split(":")
        
        # Validate API key and secret
        user = frappe.db.get_value("User", {"api_key": api_key}, "name")
        if not user:
            return {"status": "error", "message": ("Invalid API Key")}

        # Check if API secret matches
        api_secret_stored = frappe.db.get_value("User", {"api_key": api_key}, "api_secret")
        if frappe.utils.password.check_password(api_secret_stored, api_secret):
            user_info = frappe.get_doc("User", user).as_dict()

            # Remove sensitive fields from the response
            user_info.pop('api_key', None)
            user_info.pop('api_secret', None)
            return {"status": "success", "user": user_info}

        return {"status": "error", "message": ("Invalid API Secret")}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_user_info")
        return {"status": "error", "message": {str(e)}}