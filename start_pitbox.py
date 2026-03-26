import os
import subprocess
import time
import urllib.request
import logging

from pitbox_common.ports import CONTROLLER_HTTP_PORT

# Setup logging
log_dir = os.path.join(os.getcwd(), 'logs')
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
log_file = os.path.join(log_dir, 'start_pitbox.log')
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
    
    # Use repo static (RIG CONTROL CENTER) when running from dev
    script_dir = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(script_dir, 'controller', 'static')
    env = os.environ.copy()
    if os.path.isfile(os.path.join(static_dir, 'index.html')):
        env['PITBOX_STATIC_DIR'] = static_dir
    controller_process = subprocess.Popen(['python', '-m', 'controller.main'], stdout=open(stdout_log_path, 'a'), stderr=open(stderr_log_path, 'a'), env=env)
    
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

if __name__ == '__main__':
    exit_code = start_controller()
    exit(exit_code)
