import getpass
import hashlib
import subprocess
import tempfile
import time
from pathlib import Path
import pandas as pd
from tabulate import tabulate
import argparse
import json
import os
import sys
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import chrome_install_mgr
import config
import logging
from selenium.webdriver.remote.remote_connection import LOGGER

LOGGER.setLevel(logging.ERROR)

CHROMEDRIVER_URL = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
MODEL_LIST_URL = "https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess"
MAIN_AWS_SCREEN_URL = "https://aws.amazon.com/"
HEADLESS = True

AWS_ACCOUNT_ID = ""
IAM_ADMIN_USER = ""
IAM_ADMIN_PWD = ""

CACHE_DIR = Path("./cache")
CACHE_TTL = 300  # 5 minutes in seconds


# This function invokes:  aws bedrock list-foundation-models --output json
#
# returns json output.  This is the json object that gets enhanced by the
#     list-foundation-model-with-enablement-status process.
def load_model_data():
    try:
        result = subprocess.run(
            ['aws', 'bedrock', 'list-foundation-models', '--output', 'json'],
            capture_output=True, text=True
        )
        result.check_returncode()
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running AWS CLI command: {e}")
        exit(1)


# This is the code that navigates us to the AWS console.  It would have to be changed to accommodate whatever
# environment you're running in.  This uses a basic login to an admin user.  It SHOULD support MFA by asking the
# user to provide their MFA number from their authenticator, though that obviously prevents automation, so I turned off
# my MFA on my user while working on this
def login_to_console(destination_url):
    chrome_driver_path = chrome_install_mgr.ensure_chromedriver_installed()
    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={tempfile.mkdtemp()}")
    options.add_argument("--incognito")
    if HEADLESS:
        options.add_argument("--headless")
        options.add_argument("--log-level=1")
        options.add_argument("--remote-debugging-port=0")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        options.add_argument("--no-sandbox")
        options.add_argument("--silent")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.headless = True

    service_args_l = ["--silent"]
    service = Service(chrome_driver_path, service_args=service_args_l, log_output="me.log")

    driver = webdriver.Chrome(service=service, options=options)
    if config.is_verbose_mode():
        print("Navigating to " + destination_url)
    driver.get(destination_url)
    if config.is_verbose_mode():
        print("Waiting to see sign in button...")
    try:
        # Wait up to 30 seconds for the "Sign In" link to appear
        sign_in_link = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.LINK_TEXT, "Sign In"))
        )
        if config.is_verbose_mode():
            print("Found the Sign In link, clicking...")
        sign_in_link.click()

        chrome_install_mgr.wait_for_browser_settle(driver)
        if config.is_verbose_mode():
            print("Current URL: " + driver.current_url)
            print("Waiting for account field to appear...")

        account_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "account"))  # Adjust this as needed
        )

        account_field.clear()
        account_field.send_keys(str(AWS_ACCOUNT_ID))

        if config.is_verbose_mode():
            print("Waiting for IAM user field to appear...")

        user_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "username"))
        )
        user_field.clear()
        user_field.send_keys(str(IAM_ADMIN_USER))

        if config.is_verbose_mode():
            print("Waiting for IAM pwd field to appear...")

        pwd_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "password"))
        )
        pwd_field.clear()
        pwd_field.send_keys(str(IAM_ADMIN_PWD))
        time.sleep(3)

        if config.is_verbose_mode():
            print("Waiting for sign in to appear...")

        sign_in_link2 = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "signin_button"))
        )

        sign_in_link2.click()
        chrome_install_mgr.wait_for_browser_settle(driver)
        ctr = 0
        while ctr < 5 and "signin" in driver.current_url:
            time.sleep(1)
            ctr = ctr + 1

        if "oauth" in driver.current_url:
            mfa_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "mfaCode"))
            )

            mfa = input("Type your MFA code: ")
            mfa_field.clear()
            mfa_field.send_keys(mfa)
            if config.is_verbose_mode():
                print("Waiting for sign in to appear...")
            sign_in_link_x = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='mfa-submit-button']"))
            )

            sign_in_link_x.click()
            time.sleep(3)
            chrome_install_mgr.wait_for_browser_settle(driver)
            if config.is_verbose_mode():
                print("Current URL: " + driver.current_url)

    except Exception as e:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        screenshot_path = f"error_screenshot_{timestamp}.png"
        driver.save_screenshot(screenshot_path)
        print(f"Error: Sign In link not found, screen at: {screenshot_path}")
        print(e)

    finally:
        if "console" in driver.current_url and "redirect" not in driver.current_url:
            if config.is_verbose_mode():
                print(">>>Successfully landed on console URL.<<<")
            return driver
        else:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            screenshot_path = f"error_screenshot_{timestamp}.png"
            driver.save_screenshot(screenshot_path)
            raise ValueError("Did not land on console URL.  Current url=" + driver.current_url)


# this code navigates to the bedrock model list and gathers up all the installed statuses from the catalog table
def scrape_access_status(driver):
    access_status = {}
    try:
        if config.is_verbose_mode():
            print("Navigating to bedrock model list")
        driver.get(MODEL_LIST_URL)
        chrome_install_mgr.wait_for_browser_settle(driver)

        if config.is_verbose_mode():
            print("Waiting for table to appear...")
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))

        if config.is_verbose_mode():
            print("Searching table...")
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        for row in rows:
            cells_for_this_row = row.find_elements(By.TAG_NAME, "td")
            if len(cells_for_this_row) > 1 and "/" not in cells_for_this_row[1].text:
                model_id = cells_for_this_row[0].text.split("\n")[0].strip()
                status = cells_for_this_row[1].text.split("\n")[0].strip()
                access_status[model_id] = status
    except Exception as e:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        screenshot_path = f"error_screenshot_{timestamp}.png"
        driver.save_screenshot(screenshot_path)
        print(f"Error while scraping: {e}")

    return access_status

# this is the code that sets the access status node in the JSON object for a specific model
def update_access_status(models, access_status):
    for model in models['modelSummaries']:
        model_name = model['modelName']
        model['accessStatus'] = access_status.get(model_name, 'Unknown')
    return models


# this code logs in, scrapes all the enablement statuses from the bedrock catalog table, and then updates the
# json object with their current status
def enhance_foundation_model_data(input_json):
    driver = login_to_console(MAIN_AWS_SCREEN_URL)

    if config.is_verbose_mode():
        print("Scraping Access Status from AWS Console...")

    access_list = {}
    retry_ctr = 0
    while not access_list:
        access_list = scrape_access_status(driver)
        retry_ctr = retry_ctr + 1
        if retry_ctr >= 10:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            screenshot_path = f"error_screenshot_{timestamp}.png"
            driver.save_screenshot(screenshot_path)
            driver.quit()
            raise ValueError("Too many retries while trying to scrape model status screen")

    driver.quit()
    updated_models = update_access_status(input_json, access_list)
    return updated_models


# this code invokes enhance_foundation_model_data if there are no current cached copies of its output, otherwise it
# just returns a cached copy
def get_foundation_model_enablement_status(args):
    CACHE_DIR.mkdir(exist_ok=True)

    # Check for --no-cache flag
    try:
        use_cache = args.no_cache is False
    except:
        use_cache = True

    # Generate a stable cache key that ignores function pointers
    cache_key = generate_cache_key(args)

    # Cache file path
    cache_file = CACHE_DIR / f"{cache_key}.json"

    # Remove stale cache files
    clean_old_cache_files()

    # Use cache if available and allowed
    if use_cache and cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL:
        with open(cache_file, "r") as f:
            data = json.load(f)
    else:
        # Perform the expensive operations
        data = load_model_data()
        enhance_foundation_model_data(data)

        # Cache the results if caching is enabled
        if use_cache:
            with open(cache_file, "w") as f:
                json.dump(data, f)

    return data


# this is the main entry point for the list-foundation-models-with-enablement-status command
def list_foundation_model_enablement_status(args):
    data = get_foundation_model_enablement_status(args)
    # Output the final data
    output_results(data, args.output)


# This is a utility function used by get_foundation_model_enablement_status()  It generates a cache key used by the
# caching mechanism.  Nothing important to see here...
def generate_cache_key(args):
    # Convert Namespace to a dictionary
    arg_dict = vars(args)

    # Filter out function pointers and unhashable types
    filtered_args = {}
    for k, v in arg_dict.items():
        # Skip function pointers and unhashable objects
        if callable(v) or isinstance(v, (type, object)):
            continue
        filtered_args[k] = v

    # Use a stable string representation for the hash
    key_string = json.dumps(filtered_args, sort_keys=True)
    return hashlib.sha256(key_string.encode()).hexdigest()


# this is used by the caching behavior inside get_foundation_model_enablement_status().... nothing important to see here
def clean_old_cache_files():
    now = time.time()
    for file in CACHE_DIR.iterdir():
        if file.is_file() and (now - file.stat().st_mtime) > CACHE_TTL:
            file.unlink()


# this code just retrieves the current enablement status for a given model.  It is used primarily to make sure that
# the user isn't trying to enable something that's in the wrong status
def get_model_access_status(model_name, model_data):
    # Normalize the search name for case-insensitive matching
    target_name = model_name.lower()

    # Search through the model summaries
    for model in model_data.get("modelSummaries", []):
        # Check if the normalized model name matches
        if model.get("modelName", "").lower() == target_name:
            # Return the access status, defaulting to "Unknown" if not present
            return model.get("accessStatus", "Unknown")

    # Return "Unknown" if the model name was not found
    return "Unknown"


# this is just conditional Selenium code that fills a field if it exists, otherwise it does nothing.  It's used for
# optional fields that may or may not be present on the screen
def fill_text_field_if_exists(driver, field_name, text_value):
    # Attempt to find the text field
    fields = driver.find_elements(By.NAME, field_name)

    # Only fill in the first matching field, if any
    if fields:
        field = fields[0]
        field.clear()
        field.send_keys(text_value)


# this code just checks a checkbox on the screen for a given model
def click_checkbox_for_model_row(driver, model_name):
    if config.is_verbose_mode():
        print("Searching table...")
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    found = False
    for row in rows:
        # Check if the row contains the target text
        if model_name in row.text:
            found = True
            if config.is_verbose_mode():
                print(f"Found row containing {model_name}, clicking checkbox...")
            # Find the checkbox within this row
            checkbox = row.find_element(By.CSS_SELECTOR, "input[type='checkbox']")

            driver.execute_script("arguments[0].click();", checkbox)

            if config.is_verbose_mode():
                print(f"Clicked checkbox containing '{model_name}'")
            break

    if config.is_verbose_mode() and found is False:
        raise ValueError(f"No row found containing '{model_name}'")


# this is some serious hackery right here... because AWS uses some weird UI library, you can't just select a drop down,
# and there's a drop down for "industry name".  This code sets that industry name to "Other" and then fills in
# whatever the user provided in the text field for their industry.  This was done to simplify the code, which obviously
# wasn't that simple because of how AWS handles drop downs.
#
# Basically, since we can't count on the Selenium WebDriver primitives to work with this framework that AWS used, we
# have to inject a bunch of javascript into the browser document context, and let it do all the work, which is what this
# code does... and their library has some really weird requirements, like a mouse has to hover over their fake drop down
# before clicking on it, so we have to simulate a mouse down, mouse up, and then click event.
#
# Then for the click boxes for internal or external users, we have to do more hackery, where we set a quarter second
# wait after we've set the Industry fields before clicking the checkboxes.
#
# It works, but it's not pretty.  Woe unto you if you end up here having to try to debug this.
def click_dropdown_option(driver, industry_name, check_internal, check_external):
    dropdown_js = """
    function clickDropdownOption(industryName, checkInternal, checkExternal) {
        const optionText = "Other";

        function clickElement(element) {
            const rect = element.getBoundingClientRect();
            ["mouseover", "mousedown", "mouseup", "click"].forEach(type => {
                element.dispatchEvent(new MouseEvent(type, {
                    bubbles: true,
                    cancelable: true,
                    clientX: rect.left + rect.width / 2,
                    clientY: rect.top + rect.height / 2,
                    view: window
                }));
            });
        }

        const button = document.querySelector("button[id^='formField:']");
        if (!button) return;
        clickElement(button);

        new MutationObserver((mutations, observer) => {
            for (const mutation of mutations) {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType === 1 && node.getAttribute("role") === "option" && node.textContent.trim().toLowerCase().includes(optionText.toLowerCase())) {
                        clickElement(node);
                        observer.disconnect();

                        setTimeout(() => {
                            if (industryName) {
                                const input_industry = document.getElementsByName("otherIndustry")[0];
                                if (input_industry) {
                                    input_industry.focus();
                                    input_industry.setRangeText(industryName);
                                    input_industry.dispatchEvent(new InputEvent("input", { bubbles: true }));
                                    input_industry.dispatchEvent(new Event("change", { bubbles: true }));
                                }
                            }
                            if (checkInternal) {
                                const internalCheckbox = document.getElementsByName("intendedUsers.internal")[0];
                                if (internalCheckbox && !internalCheckbox.checked) internalCheckbox.click();
                            }
                            if (checkExternal) {
                                const externalCheckbox = document.getElementsByName("intendedUsers.external")[0];
                                if (externalCheckbox && !externalCheckbox.checked) externalCheckbox.click();
                            }
                        }, 250);

                        return;
                    }
                }
            }
        }).observe(document.body, { childList: true, subtree: true });
    }
    """

    # Call the function with runtime parameters
    js_call = dropdown_js + f"\nclickDropdownOption('{industry_name}', {str(check_internal).lower()}, {str(check_external).lower()})"

    # print("shipping:\n\n" + js_call)
    driver.execute_script(js_call)


# this is the code that handles all the "special fields" required by the Anthropic models (why in heaven's name did they
# do this?  What an utter waste of everyone's time and talent...)
def handle_special_fields(driver, args):
    if args.company_name is None:
        fill_text_field_if_exists(driver, "companyName",
                                  "Unknown Company")  # should really never hit this, but if the text box appears and they didn't select a Claude model this would handle it
    else:
        fill_text_field_if_exists(driver, "companyName", args.company_name)

    if args.company_website_url is None:
        fill_text_field_if_exists(driver, "companyWebsite",
                                  "https://www.nobodyknows.com")  # should really never hit this, but if the text box appears and they didn't select a Claude model this would handle it
    else:
        fill_text_field_if_exists(driver, "companyWebsite", args.company_website_url)

    if args.use_case_description is None:
        fill_text_field_if_exists(driver, "useCases",
                                  "Don't know what use cases.  Experimenting right now.")  # should really never hit this, but if the text box appears and they didn't select a Claude model this would handle it
    else:
        fill_text_field_if_exists(driver, "useCases", args.use_case_description)

    industry_name = "Unknown Industry"
    if args.industry is not None:
        industry_name = args.industry

    check_internal = True
    check_external = True

    if args.internal_employees is None:
        check_internal = False
    else:
        if args.internal_employees == str(0) or str(args.internal_employees).lower() == "false":
            check_internal = False

    if args.external_users is None:
        check_external = False
    else:
        if args.external_users == str(0) or str(args.external_users).lower() == "false":
            check_internal = False

    if check_internal is False and check_external is False:
        check_internal = True  # at least one must be true

    click_dropdown_option(driver, industry_name, check_internal, check_external)  # magic and mayhem here... beware.


# this is the main entry point for the enable-foundation-model command line functionality.
def enable_foundation_model(args):
    if not args.model_name:
        print("Error: The --model-name parameter is required.")
        sys.exit(1)

    # If the model name contains 'Claude', make all optional parameters required
    if "Claude" in args.model_name:
        required_fields = [
            "company_name", "company_website_url", "industry",
            "internal_employees", "external_users", "use_case_description"
        ]
        missing_fields = [field for field in required_fields if not getattr(args, field)]
        if missing_fields:
            print(
                f"Error: The following parameters are required when the model name contains 'Claude': {', '.join(missing_fields)}")
            sys.exit(1)

    if config.is_verbose_mode():
        print(f"Checking foundation model: '{args.model_name}' activation status")

    current_models = get_foundation_model_enablement_status(args)
    model_status = get_model_access_status(args.model_name, current_models)

    if config.is_verbose_mode():
        print(f"Model status: {model_status}")
    if model_status != "Available to request":
        print(f"Model {args.model_name} is not in 'Available to request' status.  Status = {model_status}")
        sys.exit(2)

    try:
        driver = login_to_console(MAIN_AWS_SCREEN_URL)
        if config.is_verbose_mode():
            print("Navigating to bedrock model list")
        driver.get(MODEL_LIST_URL)
        chrome_install_mgr.wait_for_browser_settle(driver)

        if config.is_verbose_mode():
            print("Waiting for table to appear...")
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))

        if config.is_verbose_mode():
            print("Waiting for Enable Specific button...")

        enable_specific_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[@data-testid='modify-button' or @data-testid='enable-specific-button']")
            )
        )

        driver.execute_script("arguments[0].click();", enable_specific_button)
        time.sleep(3)

        click_checkbox_for_model_row(driver, args.model_name)

        next_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[.//text()[contains(., 'Next')]]")
            )
        )

        driver.execute_script("arguments[0].click();", next_button)
        time.sleep(3)

        chrome_install_mgr.wait_for_browser_settle(driver)

        # --------------------------------------------------------------------------
        # Claude specific nonsense begins here (some seriously complicated javascript
        # injection happens here where we ship a bunch of code over to the browser
        # just so we can select out of a drop down and click a couple of check boxes
        # in AWS's weird proprietary UI library that doesn't use standard check box
        # and drop down dynamics...)
        # --------------------------------------------------------------------------
        handle_special_fields(driver, args)
        # --------------------------------------------------------------------------
        # Claude specific nonsense ends here
        # --------------------------------------------------------------------------

        back_at_access_screen = False
        WaitCtr = 0

        while not back_at_access_screen and WaitCtr < 4:
            WaitCtr = WaitCtr + 1
            try:
                submit_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Submit') or contains(., 'Next')]"))
                )
                driver.execute_script("arguments[0].click();", submit_button)
            except:
                pass

            enable_specific_button = None
            try:
                enable_specific_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//*[@data-testid='modify-button' or @data-testid='enable-specific-button']")
                    )
                )
            except:
                pass

            if enable_specific_button is not None:
                back_at_access_screen = True

        if WaitCtr >= 4:
            raise ValueError(f"Unable to submit request to enable model {args.model_name}")
        else:
            print(f"Model {args.model_name} enablement request submitted")


    except Exception as e:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        screenshot_path = f"error_screenshot_{timestamp}.png"
        driver.save_screenshot(screenshot_path)
        print(f"Error while scraping: {e}")

    print(f"Enabling {args.model_name}")
    time.sleep(5)
    driver.quit()


def output_results(data, output_format):
    if output_format == "json":
        print(json.dumps(data, indent=4))
    elif output_format == "table":
        # Extract and normalize the required fields
        models = data.get("modelSummaries", [])
        table_data = [
            {
                "Model Name": m.get("modelName", ""),
                "Model ID": m.get("modelId", ""),
                "Provider Name": m.get("providerName", ""),
                "Model Lifecycle Status": m.get("modelLifecycle", {}).get("status", ""),
                "Access Status": m.get("accessStatus", "")
            }
            for m in models
        ]
        df = pd.DataFrame(table_data)
        print(tabulate(df, headers="keys", tablefmt="grid"))
    elif output_format == "text":
        for item in data:
            print("".join([f"{k}: {v}" for k, v in item.items()]))
            print()
    else:
        print(f"Error: Unsupported output format '{output_format}'")


# Where the magic begins...
def main():
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

    parser = argparse.ArgumentParser(
        description="AWS Bedrock CLI - Custom Extensions",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )

    subparsers = parser.add_subparsers(dest="command")

    # list-foundation-model-enablement-status command
    list_parser = subparsers.add_parser(
        "list-foundation-models-with-enablement-status",
        help="List foundation models with enablement status"
    )
    list_parser.add_argument(
        "--output",
        choices=["json", "table", "text"],
        default="json",
        help="Output format (json, table, text)"
    )
    list_parser.add_argument(
        "--no-cache",
        required=False,
        help="Force cache refresh",
        action="store_true"
    )
    list_parser.set_defaults(func=list_foundation_model_enablement_status)

    # enable-foundation-model command
    enable_parser = subparsers.add_parser(
        "enable-foundation-model",
        help="Enable a foundation model"
    )
    enable_parser.add_argument(
        "--model-name",
        required=True,
        help="Name of the foundation model to enable"
    )
    enable_parser.add_argument(
        "--company-name",
        required=False,
        help="Company name"
    )
    enable_parser.add_argument(
        "--company-website-url",
        required=False,
        help="Company website URL"
    )
    enable_parser.add_argument(
        "--industry",
        required=False,
        help="Industry"
    )
    enable_parser.add_argument(
        "--internal-employees",
        required=False,
        help="Indicates that the model will be used by internal employees"
    )
    enable_parser.add_argument(
        "--external-users",
        required=False,
        help="Indicates that the model will be used by external users"
    )
    enable_parser.add_argument(
        "--use-case-description",
        required=False,
        help="Description of the use case"
    )
    enable_parser.add_argument(
        "--output",
        choices=["json", "table", "text"],
        default="json",
        help="Output format (json, table, text)"
    )
    enable_parser.set_defaults(func=enable_foundation_model)

    args = parser.parse_args()
    config.set_verbose_mode(args.verbose)

    # make sure necessary environment variables exist
    global AWS_ACCOUNT_ID
    AWS_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID")
    if AWS_ACCOUNT_ID is None:
        AWS_ACCOUNT_ID = input("Type the account number you are using: ")
    else:
        if config.is_verbose_mode():
            print(f"Found AWS_ACCOUNT_ID in environment variables: {AWS_ACCOUNT_ID}")
    global IAM_ADMIN_USER
    IAM_ADMIN_USER = os.environ.get("IAM_ADMIN_USER")
    if IAM_ADMIN_USER is None:
        IAM_ADMIN_USER = input("Type the admin user id you are using: ")
    else:
        if config.is_verbose_mode():
            print(f"Found IAM_ADMIN_USER in environment variables: {IAM_ADMIN_USER}")
    global IAM_ADMIN_PWD
    IAM_ADMIN_PWD = os.environ.get("IAM_ADMIN_PWD")
    if IAM_ADMIN_PWD is None:
        IAM_ADMIN_PWD = getpass.getpass("Type the password for user: " + IAM_ADMIN_USER + "/" + AWS_ACCOUNT_ID + "> ")
    else:
        if config.is_verbose_mode():
            half_len = int((len(IAM_ADMIN_PWD) / 2) + 1)
            partially_hidden_pwd = '*' * half_len
            partially_hidden_pwd = partially_hidden_pwd + IAM_ADMIN_PWD[-half_len:]
            print(f"Found IAM_ADMIN_PWD in an environment variable: {partially_hidden_pwd}")

    if config.is_verbose_mode():
        print("Verbose mode enabled.")

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
