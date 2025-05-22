import os
import shutil
import subprocess
import platform
import time
import zipfile
import requests
from selenium.webdriver.support.wait import WebDriverWait
import config

CHROMEDRIVER_URL = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"


def wait_for_browser_settle(driver):
    if config.is_verbose_mode():
        print("Waiting for DOM to settle...")
    time.sleep(3)
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def download_chromedriver(version: object) -> object:
    major_version = version.split(".")[0]
    if config.is_verbose_mode():
        print(f"Detected Chrome major version: {major_version}")

    # Fetch available versions
    available_versions = fetch_available_versions()

    # Determine the correct platform key
    platform_name = platform.system().lower()
    if platform_name == "windows":
        arch = platform.architecture()[0]
        platform_key = "win32" if arch == "32bit" else "win64"
    elif platform_name == "darwin":
        platform_key = "mac-arm64" if "arm" in platform.processor().lower() else "mac-x64"
    else:
        platform_key = "linux64"

    # Find the closest matching version
    download_url = find_closest_version(version, available_versions, platform_key)
    if not download_url:
        raise RuntimeError(f"No matching ChromeDriver found for Chrome version {version} on platform {platform_key}")

    if config.is_verbose_mode():
        print(f"Downloading ChromeDriver from {download_url}...")

    zip_file_name = download_url.split("/")[-1]

    ctr = 0
    with requests.get(download_url, stream=True) as r:
        r.raise_for_status()
        with open(zip_file_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                ctr = ctr + 1
                if(ctr % 20 == 0):
                    if config.is_verbose_mode():
                        print(".", end="")

    if config.is_verbose_mode():
        print("\nExtracting ChromeDriver...")

    extract_flat(zip_file_name)

    os.remove(zip_file_name)

    if config.is_verbose_mode():
        print("Extraction complete.")


def extract_flat(zip_file_name, target_dir="."):
    with zipfile.ZipFile(zip_file_name, 'r') as zip_ref:
        for member in zip_ref.namelist():
            # Extract only the file name, ignoring any path
            filename = os.path.basename(member)
            if filename:  # Ignore empty directory entries
                source = zip_ref.open(member)
                target = open(os.path.join(target_dir, filename), "wb")
                with source, target:
                    shutil.copyfileobj(source, target)


def find_closest_version(chrome_version, available_versions, platform_key):
    major, minor, build, patch = map(int, chrome_version.split("."))
    closest_version = None
    closest_url = None
    smallest_difference = float('inf')

    for v in available_versions["versions"]:
        # Split the candidate version into components
        c_major, c_minor, c_build, c_patch = map(int, v["version"].split("."))

        # Check if the major version matches
        if c_major == major:
            # Calculate the version difference
            difference = abs((c_minor - minor) * 1_000_000 + (c_build - build) * 1_000 + (c_patch - patch))

            # Find the closest matching platform
            for dl in v["downloads"]["chromedriver"]:
                if dl["platform"] == platform_key:
                    # If this is the closest version so far, update
                    if difference < smallest_difference:
                        smallest_difference = difference
                        closest_version = v["version"]
                        closest_url = dl["url"]

    return closest_url


def fetch_available_versions():
    if config.is_verbose_mode():
        print("Fetching available ChromeDriver versions...")
    response = requests.get(CHROMEDRIVER_URL)
    response.raise_for_status()
    return response.json()


def get_chrome_version():
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["reg", "query", r"HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon", "/v", "version"],
                capture_output=True, text=True, check=True
            )
            version_line = result.stdout.strip().split("\n")[-1]
            version = version_line.split()[-1]
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"],
                capture_output=True, text=True, check=True
            )
            version = result.stdout.strip().split()[-1]
        else:
            result = subprocess.run(
                ["google-chrome", "--version"],
                capture_output=True, text=True, check=True
            )
            version = result.stdout.strip().split()[-1]

        return version
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Error fetching Chrome version: {e}")


def ensure_chromedriver_installed():
    chromedriver_path = shutil.which("chromedriver")
    if chromedriver_path:
        if config.is_verbose_mode():
            print(f"Found ChromeDriver at: {chromedriver_path}")
        return chromedriver_path

    if config.is_verbose_mode():
        print("ChromeDriver not found in PATH. Attempting to download...")
    chrome_version = get_chrome_version()
    download_chromedriver(chrome_version)

    chromedriver_path = shutil.which("chromedriver")
    if not chromedriver_path:
        raise RuntimeError("ChromeDriver installation failed or is not in the PATH.")

    if config.is_verbose_mode():
        print(f"ChromeDriver installed successfully at: {chromedriver_path}")
    return chromedriver_path
