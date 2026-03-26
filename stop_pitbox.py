import os
import subprocess
import logging

# Setup logging
log_dir = os.path.join(os.getcwd(), 'logs')
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
log_file = os.path.join(log_dir, 'stop_pitbox.log')
logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s - %(levelname)s - [PITBOX] - %(message)s')

def stop_process(name):
    pid_path = os.path.join(os.getcwd(), '.pitbox_pids', f'{name}.pid')
    if not os.path.exists(pid_path):
        logging.info(f"{name} is not running.")
        print(f"{name} is not running.")
        return
    
    with open(pid_path, 'r') as f:
        pid = int(f.read().strip())
    
    try:
        subprocess.run(['taskkill', '/PID', str(pid), '/F'], check=True)
        logging.info(f"{name} with PID {pid} stopped successfully.")
        print(f"{name} with PID {pid} stopped successfully.")
    except subprocess.CalledProcessError as e:
        if "The process with PID" in str(e) and "does not exist." in str(e):
            logging.info(f"{name} with PID {pid} is already stopped.")
            print(f"{name} with PID {pid} is already stopped.")
        else:
            logging.error(f"Failed to stop {name} with PID {pid}.")
            print(f"Failed to stop {name} with PID {pid}.")
    finally:
        if os.path.exists(pid_path):
            os.remove(pid_path)
            logging.info(f"Deleted PID file for {name}.")
            print(f"Deleted PID file for {name}.")

if __name__ == '__main__':
    import sys
    if '--all' in sys.argv:
        stop_process('controller')
        stop_process('agent')
    else:
        stop_process('controller')
