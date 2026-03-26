import os
import subprocess
import urllib.request
import logging
import time
import sys

from pitbox_common.ports import CONTROLLER_HTTP_PORT

# Setup logging
log_dir = os.path.join(os.getcwd(), 'logs')
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
log_file = os.path.join(log_dir, 'pitbox_selftest.log')
logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s - %(levelname)s - [PITBOX] - %(message)s')

def start_controller():
    stdout_log_path = os.path.join(os.getcwd(), '.pitbox_pids', 'controller.stdout.log')
    stderr_log_path = os.path.join(os.getcwd(), '.pitbox_pids', 'controller.stderr.log')
    
    if not os.path.exists(os.path.dirname(stdout_log_path)):
        os.makedirs(os.path.dirname(stdout_log_path))
    
    # Check if controller is already running
    if is_controller_running():
        logging.info("[PITBOX] Controller already running on port %s. Not starting another instance.", CONTROLLER_HTTP_PORT)
        print(f"[PITBOX] Controller already running on port {CONTROLLER_HTTP_PORT}. Not starting another instance.")
        return 0
    
    controller_process = subprocess.Popen(['python', '-m', 'controller.main'], stdout=open(stdout_log_path, 'a'), stderr=open(stderr_log_path, 'a'))
    
    logging.info(f"Controller started with PID: {controller_process.pid}")
    
    time.sleep(1)  # Wait for a short delay to check if the process is still running
    if controller_process.poll() is not None:
        logging.error("[PITBOX] Controller crashed immediately.")
        print("[PITBOX] Controller crashed immediately.")
        print(f"Check stderr log at: {stderr_log_path}")
        print(f"Check stdout log at: {stdout_log_path}")
        return 1
    
    if is_controller_ready():
        write_pid_file('controller', controller_process.pid)
        print("Controller is ready.")
        return 0
    
    logging.error("Controller did not start within 15 seconds. Check stderr log at: {}".format(stderr_log_path))
    print(f"Controller did not start within 15 seconds. Check stderr log at: {stderr_log_path}")
    return 1

def is_controller_running():
    try:
        response = urllib.request.urlopen(f'http://127.0.0.1:{CONTROLLER_HTTP_PORT}/status')
        return response.getcode() == 200
    except Exception as e:
        logging.error("Failed to reach controller on port %s: %s", CONTROLLER_HTTP_PORT, e)
        return False

def is_controller_ready():
    try:
        response = urllib.request.urlopen(f'http://127.0.0.1:{CONTROLLER_HTTP_PORT}/status')
        return response.getcode() == 200
    except Exception as e:
        logging.error("Failed to reach controller on port %s: %s", CONTROLLER_HTTP_PORT, e)
        return False

def write_pid_file(name, pid):
    with open(os.path.join(os.getcwd(), '.pitbox_pids', f'{name}.pid'), 'w') as f:
        f.write(str(pid))

def check_gui_endpoints(port):
    try:
        response = urllib.request.urlopen(f'http://127.0.0.1:{port}/')
        if response.getcode() == 200 and response.headers['Content-Type'] == 'text/html':
            logging.info("GUI endpoint / is working.")
            print("GUI endpoint / is working.")
            
            # Check index.html content
            with open(os.path.join(os.getcwd(), 'controller', 'static', 'index.html'), 'rb') as file:
                expected_content = file.read()
            if response.read() == expected_content:
                logging.info("Index.html content matches.")
                print("Index.html content matches.")
            else:
                logging.error("Index.html content does not match.")
                print("Index.html content does not match.")
                return False
        else:
            logging.error("GUI endpoint / failed or wrong Content-Type.")
            print("GUI endpoint / failed or wrong Content-Type.")
            return False
    except Exception as e:
        logging.error(f"Failed to reach GUI endpoint /: {e}")
        print(f"Failed to reach GUI endpoint /: {e}")
        return False

    try:
        response = urllib.request.urlopen(f'http://127.0.0.1:{port}/app.js')
        if response.getcode() == 200 and response.headers['Content-Type'] == 'application/javascript':
            logging.info("GUI endpoint /app.js is working.")
            print("GUI endpoint /app.js is working.")
        else:
            logging.error("GUI endpoint /app.js failed or wrong Content-Type.")
            print("GUI endpoint /app.js failed or wrong Content-Type.")
            return False
    except Exception as e:
        logging.error(f"Failed to reach GUI endpoint /app.js: {e}")
        print(f"Failed to reach GUI endpoint /app.js: {e}")
        return False

    try:
        response = urllib.request.urlopen(f'http://127.0.0.1:{port}/styles.css')
        if response.getcode() == 200 and response.headers['Content-Type'] == 'text/css':
            logging.info("GUI endpoint /styles.css is working.")
            print("GUI endpoint /styles.css is working.")
        else:
            logging.error("GUI endpoint /styles.css failed or wrong Content-Type.")
            print("GUI endpoint /styles.css failed or wrong Content-Type.")
            return False
    except Exception as e:
        logging.error(f"Failed to reach GUI endpoint /styles.css: {e}")
        print(f"Failed to reach GUI endpoint /styles.css: {e}")
        return False

    return True

def main():
    import argparse
    parser = argparse.ArgumentParser(description="PitBox Self Test Script")
    parser.add_argument('--no-start', action='store_true', help="Do not start the controller if it's not running.")
    parser.add_argument('--stop-after', action='store_true', help="Stop the controller after testing.")
    args = parser.parse_args()

    if not is_controller_running() and not args.no_start:
        exit_code = start_controller()
        if exit_code != 0:
            return exit_code

    port = CONTROLLER_HTTP_PORT
    try:
        response = urllib.request.urlopen(f'http://127.0.0.1:{port}/status')
        if response.getcode() == 200 and b"Controller is running." in response.read():
            logging.info("Status endpoint is working.")
            print("Status endpoint is working.")
        else:
            logging.error("Status endpoint failed.")
            print("Status endpoint failed.")
            return 1
    except Exception as e:
        logging.error(f"Failed to reach status endpoint: {e}")
        print(f"Failed to reach status endpoint: {e}")
        return 1

    if not check_gui_endpoints(port):
        logging.error("GUI checks failed.")
        print("GUI checks failed.")
        return 1

    logging.info("All tests passed.")
    print("All tests passed.")

    if args.stop_after:
        subprocess.run([sys.executable, 'stop_pitbox.py', '--all'])

    return 0

if __name__ == '__main__':
    raise SystemExit(main())
