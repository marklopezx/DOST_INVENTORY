import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import datetime
from supabase import create_client, Client

app = Flask(__name__)
app.secret_key = "supersecretkey" 

# --- CONFIGURATION ---
SUPABASE_URL = "https://oamboqnmmzsrsrksgfyu.supabase.co"
SUPABASE_KEY = "sb_publishable_XzznL3gVhOhWoZCSLg8LGA_jFeDBe_Q"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- PUBLIC LANDING GATEWAY ---
@app.route('/')
def index():
    # If a staff member or admin is already logged in, skip the landing page entirely
    if session.get('user_id'):
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('user_home'))
        
    # Extract modal tracking states, defaulting to False if not present
    reg_mode = session.pop('reg_mode', False)
    show_modal = session.pop('show_modal', False)
    
    # Cleanly render the unified modal landing dashboard template
    return render_template('landing.html', reg_mode=reg_mode, show_modal=show_modal)

# --- AUTHENTICATION INTERACTIVE TRIGGERS ---
@app.route('/login_page')
def login_page():
    session['reg_mode'] = False
    session['show_modal'] = True
    return redirect(url_for('index'))

@app.route('/signup_page')
def signup_page():
    session['reg_mode'] = True
    session['show_modal'] = True
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    first_name = request.form.get('first_name').strip()
    last_name = request.form.get('last_name').strip()
    email = request.form.get('email').strip()
    password = request.form.get('password')

    try:
        # 1. Sign up the user in Supabase Auth 
        # We store the names in user_metadata so the database trigger can read them!
        auth_response = supabase.auth.sign_up({
            "email": email,
            "password": password,
            "options": {
                "data": {
                    "first_name": first_name,
                    "last_name": last_name
                }
            }
        })
        
        if not auth_response.user:
            raise Exception("Auth registration failed to return a valid user payload.")

        # Registration successful -> Prompt verification requirement and toggle login modal view
        session['reg_mode'] = False
        session['show_modal'] = True
        flash("Registration successful! An email verification link has been sent. Please check your inbox before logging in.")
        return redirect(url_for('index'))

    except Exception as e:
        print(f"Detailed Signup Error: {str(e)}")
        # Error caught -> auto-reopen the signup modal layout to show the flash warning details
        session['reg_mode'] = True
        session['show_modal'] = True
        flash(f"Registration Error: {str(e)}")
        return redirect(url_for('index'))

@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email').strip()
    password = request.form.get('password')

    try:
        print(f"Attempting sign-in for: {email}")
        # 1. Authenticate user cleanly
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        
        # Guard clause to safely extract the user id across different library versions
        if hasattr(response, 'user') and response.user:
            user_id = response.user.id
            user_payload = response.user
        elif hasattr(response, 'session') and response.session and response.session.user:
            user_id = response.session.user.id
            user_payload = response.session.user
        else:
            raise Exception("Could not parse user ID properties from Supabase response.")

        # --- EMAIL VERIFICATION GUARD INTERCEPT ---
        # Checks if email confirmation is missing or unconfirmed inside the auth user metadata instance
        if hasattr(user_payload, 'confirmed_at') and not getattr(user_payload, 'confirmed_at'):
            # Clear any partial sessions to force explicit login flow later
            supabase.auth.sign_out()
            session['reg_mode'] = False
            session['show_modal'] = True
            flash("Login Blocked: Your email address has not been verified yet. Please check your inbox.")
            return redirect(url_for('index'))

        print(f"Supabase Auth match found! User UUID: {user_id}")
        
        # 2. Fetch profile role safely (WITHOUT using .single() to avoid crashing the code)
        profile_query = supabase.table("profiles").select("role").eq("id", str(user_id)).execute()
        
        # Check if the query returned an empty list instead of crashing
        if not profile_query.data or len(profile_query.data) == 0:
            print(f"CRITICAL: User {user_id} exists in Auth, but has NO row in profiles table!")
            session['reg_mode'] = False
            session['show_modal'] = True
            flash("Profile record missing. Please contact your system administrator.")
            return redirect(url_for('index'))

        # Safe extraction of the role string
        user_role = profile_query.data[0]['role']
        
        # 3. Establish Flask session cookies
        session['user_id'] = user_id
        session['role'] = user_role
        session['email'] = email

        print(f"Login completely successful! Assigned Role: {user_role}")

        # 4. Route accurately based on database assignment
        if user_role == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('user_home'))
            
    except Exception as e:
        print(f"LOGIN EXCEPTION CAUGHT: {str(e)}")
        
        # Intercept specific email unverified warning string components if thrown directly by the client API instance
        error_msg = str(e).lower()
        if "email_not_confirmed" in error_msg or "confirm your email" in error_msg:
            flash("Login Failed: Your email address has not been verified yet. Please check your inbox.")
        else:
            flash(f"Login Failed: {str(e)}")
            
        # Login failed -> pop the log-in modal window back up immediately with validation context
        session['reg_mode'] = False
        session['show_modal'] = True
        return redirect(url_for('index'))

# --- ADMINISTRATIVE CONTROL DASHBOARD ---
@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    try:
        inv_response = supabase.table("equipment").select("*").order("name").execute()
        
        req_response = supabase.table("requests")\
            .select("*, profiles:user_id(first_name, last_name), equipment:equipment_id(name)")\
            .eq("status", "Pending")\
            .order("id", desc=True)\
            .execute()
            
        history_response = supabase.table("requests")\
            .select("*, profiles:user_id(first_name, last_name), equipment:equipment_id(name)")\
            .neq("status", "Pending")\
            .order("updated_at", desc=True)\
            .execute()

        return render_template('admin_dashboard.html', 
                               inventory=inv_response.data, 
                               pending=req_response.data,
                               history=history_response.data)
    except Exception as e:
        print(f"Dashboard Error: {e}")
        return "Error loading dashboard", 500

@app.route('/add_equipment', methods=['POST'])
def add_equipment():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    name = request.form.get('name')
    quantity = request.form.get('quantity')
    category = request.form.get('category')
    try:
        supabase.table("equipment").insert({
            "name": name,
            "quantity": int(quantity),
            "category": category,
            "status": "Available" if int(quantity) > 0 else "Out of Stock"
        }).execute()
        flash("Item added successfully!")
        return redirect(url_for('admin_dashboard'))
    except Exception as e:
        return f"Database Error: {str(e)}", 500

@app.route('/delete_equipment/<int:item_id>', methods=['POST'])
def delete_equipment(item_id):
    if session.get('role') != 'admin': 
        return redirect(url_for('login_page'))
    try:
        supabase.table("equipment").delete().eq("id", item_id).execute()
        flash("Equipment removed successfully.")
    except Exception as e:
        flash("Error deleting item.")
    return redirect(url_for('admin_dashboard'))

@app.route('/handle_request/<int:req_id>/<string:new_status>', methods=['POST'])
def handle_request(req_id, new_status):
    if session.get('role') != 'admin': 
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        supabase.table("requests").update({"status": new_status}).eq("id", req_id).execute()
        
        if new_status == "Approved":
            req_data = supabase.table("requests").select("equipment_id, requested_quantity").eq("id", req_id).single().execute()
            item_id = req_data.data['equipment_id']
            qty_needed = req_data.data['requested_quantity']
            
            item_data = supabase.table("equipment").select("quantity").eq("id", item_id).single().execute()
            new_qty = item_data.data['quantity'] - qty_needed
            
            supabase.table("equipment").update({
                "quantity": new_qty, 
                "status": "Available" if new_qty > 0 else "Out of Stock"
            }).eq("id", item_id).execute()

        return jsonify({"success": True, "status": new_status})
    except Exception as e:
        print(f"Request Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/update_stock/<int:item_id>/<string:action>', methods=['POST'])
def update_stock(item_id, action):
    if session.get('role') != 'admin': 
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        item = supabase.table("equipment").select("quantity").eq("id", item_id).single().execute()
        current_qty = item.data['quantity']
        new_qty = current_qty + 1 if action == 'add' else current_qty - 1
        if new_qty < 0: new_qty = 0
        status = "Available" if new_qty > 0 else "Out of Stock"
        supabase.table("equipment").update({"quantity": new_qty, "status": status}).eq("id", item_id).execute()
        return jsonify({"success": True, "new_qty": new_qty, "status": status})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- USER CHANNELS MANAGEMENT ---
@app.route('/user_home')
def user_home():
    user_id = session.get('user_id')
    if not user_id: 
        print("User Home Access Denied: No user_id session token.")
        flash("Please log in first.")
        return redirect(url_for('login_page'))
        
    try:
        inventory_data = supabase.table("equipment").select("*").order("name").execute()
        request_data = supabase.table("requests")\
            .select("*, equipment:equipment_id(name)")\
            .eq("user_id", str(user_id))\
            .order("updated_at", desc=True)\
            .execute()

        pending_requests = []
        history_requests = []
        for req in request_data.data:
            raw_target_time = req.get('updated_at') if req.get('status') != 'Pending' else req.get('created_at')
            if not raw_target_time:
                raw_target_time = datetime.now().isoformat()

            try:
                clean_display_date = datetime.fromisoformat(raw_target_time.replace("Z", "+00:00")).strftime('%b %d, %Y')
            except Exception:
                clean_display_date = raw_target_time.split('T')[0]

            formatted = {
                'id': req.get('id'),
                'date': clean_display_date,
                'iso_date': raw_target_time,
                'item_name': req.get('equipment', {}).get('name', 'Unknown Item'),
                'quantity': req.get('requested_quantity', 0),
                'status': req.get('status', 'Pending')
            }
            if formatted['status'] == "Pending": 
                pending_requests.append(formatted)
            else: 
                history_requests.append(formatted)

        return render_template('user_home.html', 
                               inventory=inventory_data.data, 
                               pending_requests=pending_requests,
                               history_requests=history_requests)
                               
    except Exception as e:
        print(f"User Home Error: {str(e)}")
        flash(f"Error loading user home panel: {str(e)}")
        return redirect(url_for('login_page'))

@app.route('/request_item/<int:item_id>', methods=['POST'])
def request_item(item_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized session access."}), 401

    purpose = request.form.get('purpose', 'Official Business')
    qty_val = request.form.get('requested_quantity')
    qty = int(qty_val) if qty_val and qty_val.isdigit() else 1
    
    formatted_ui_date = datetime.now().strftime('%b %d, %Y')

    try:
        supabase.table("requests").insert({
            "user_id": str(user_id), 
            "equipment_id": item_id, 
            "purpose": purpose, 
            "requested_quantity": qty, 
            "status": "Pending"
        }).execute()
        
        return jsonify({
            "success": True, 
            "date": formatted_ui_date,
            "message": "Requested successfully!"
        })
        
    except Exception as e:
        print(f"Async Asset Placement Exception: {str(e)}")
        return jsonify({
            "success": False, 
            "error": "Database entry processing error."
        }), 500
    
@app.route('/profile')
def profile_view():
    user_id = session.get('user_id')
    if not user_id: 
        flash("Please log in first.")
        return redirect(url_for('login_page'))
        
    try:
        profile_response = supabase.table("profiles").select("*").eq("id", str(user_id)).execute()
        user_profile = profile_response.data[0] if profile_response.data else None
        
        avatar_url = user_profile.get('avatar_url') if user_profile else None

        request_data = supabase.table("requests").select("status").eq("user_id", str(user_id)).execute()
        
        total_requests_count = 0
        pending_count = 0
        approved_count = 0
        rejected_count = 0
        
        if request_data.data:
            total_requests_count = len(request_data.data)
            for r in request_data.data:
                status = r.get('status')
                if status == 'Pending':
                    pending_count += 1
                elif status == 'Approved':
                    approved_count += 1
                elif status == 'Rejected':
                    rejected_count += 1

        current_user_stub = {"email": session.get('email', 'user@dost.gov.ph')}

        return render_template('profile.html', 
                               user_profile=user_profile,
                               current_user=current_user_stub,
                               total_requests_count=total_requests_count,
                               pending_count=pending_count,
                               approved_count=approved_count,
                               rejected_count=rejected_count,
                               avatar_url=avatar_url)
                               
    except Exception as e:
        print(f"Profile Core Display Error: {str(e)}")
        flash("Error loading profile configuration parameters.")
        return redirect(url_for('user_home'))
    
@app.route('/update_profile_name', methods=['POST'])
def update_profile_name():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    first_name = request.form.get('first_name')
    last_name = request.form.get('last_name')
    
    if not first_name or not last_name:
        return jsonify({"success": False, "error": "Missing input data fields."}), 400
        
    try:
        supabase.table("profiles").update({
            "first_name": first_name.strip(),
            "last_name": last_name.strip()
        }).eq("id", str(user_id)).execute()
        
        return jsonify({"success": True})
    except Exception as e:
        print(f"Supabase update crash error log: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500
    
@app.route('/change_password', methods=['POST'])
def change_password():
    user_id = session.get('user_id')
    email = session.get('email')
    
    if not user_id or not email:
        return jsonify({"success": False, "error": "Unauthorized access window. Please re-login."}), 401

    old_password = request.form.get('old_password')
    new_password = request.form.get('password')

    if not old_password or not new_password:
        return jsonify({"success": False, "error": "All password fields are required."}), 400

    if len(new_password) < 6:
        return jsonify({"success": False, "error": "New password must be at least 6 characters long."}), 400

    try:
        from supabase import create_client as isolated_client
        from supabase.lib.client_options import SyncClientOptions
        
        target_url = os.environ.get("SUPABASE_URL") or globals().get("SUPABASE_URL") or globals().get("url")
        target_key = os.environ.get("SUPABASE_ANON_KEY") or globals().get("SUPABASE_KEY") or globals().get("key")
        
        if not target_url or not target_key:
            return jsonify({"success": False, "error": "Backend Configuration Error: Supabase keys not found."}), 500

        clean_options = SyncClientOptions(auto_refresh_token=False, persist_session=False)
        temp_client = isolated_client(target_url, target_key, options=clean_options)
        
        try:
            temp_client.auth.sign_in_with_password({"email": email, "password": old_password})
        except Exception as auth_err:
            print(f"Password Check Failed for {email}: {str(auth_err)}")
            return jsonify({"success": False, "error": "The old password you entered is incorrect."}), 400

        supabase.auth.update_user({"password": new_password})
        return jsonify({"success": True, "message": "Password updated successfully."})
        
    except Exception as e:
        print("\n=== CRITICAL PASSWORD ROUTE ERROR ===")
        print(str(e))
        print("======================================\n")
        return jsonify({"success": False, "error": f"Internal System Fault: {str(e)}"}), 500
    
@app.route('/upload_avatar', methods=['POST'])
def upload_avatar():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized session window."}), 401

    if 'avatar' not in request.files:
        return jsonify({"success": False, "error": "No file payload parsed inside transmission."}), 400
        
    file = request.files['avatar']
    if file.filename == '':
        return jsonify({"success": False, "error": "No active target file selected."}), 400

    try:
        file_mime = file.content_type
        file_data = file.read()
        
        ext = os.path.splitext(file.filename)[1].lower() or '.png'
        file_path = f"{user_id}/avatar{ext}"

        # Safe multi-version storage bucket upload handler
        try:
            supabase.storage.from_("avatars").upload(
                path=file_path,
                file=file_data,
                file_options={
                    "content-type": file_mime, 
                    "x-upsert": "true",
                    "cache-control": "0"
                }
            )
        except Exception as storage_err:
            # Fallback wrapper strategy if target file record exists
            if "already exists" in str(storage_err).lower() or "duplicate" in str(storage_err).lower():
                supabase.storage.from_("avatars").update(
                    path=file_path,
                    file=file_data,
                    file_options={"content-type": file_mime}
                )
            else:
                raise storage_err

        # Construct public asset location link
        public_url = supabase.storage.from_("avatars").get_public_url(file_path)
        
        # Append an dynamic execution epoch timestamp to bust local browser cache elements
        timestamped_url = f"{public_url}?t={int(datetime.now().timestamp())}"

        # Synchronize back to profiles table data instance mapping
        supabase.table("profiles").update({"avatar_url": timestamped_url}).eq("id", str(user_id)).execute()

        return jsonify({"success": True, "avatar_url": timestamped_url})

    except Exception as e:
        print(f"File Storage Placement Exception: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)