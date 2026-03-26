import os
import subprocess
import logging

# Setup logging
log_dir = os.path.join(os.getcwd(), 'logs')
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
log_file = os.path.join(log_dir, 'verify_acs.log')
logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s - %(levelname)s - [PITBOX] - %(message)s')

def verify_acs():
    output = subprocess.check_output(['tasklist']).decode('utf-8')
    acs_pids = []
    
    for line in output.split('\n'):
        if 'acs.exe' in line:
            parts = line.split()
            pid = parts[1]
            acs_pids.append(pid)
    
    if acs_pids:
        logging.info(f"acs.exe is running with PID(s): {', '.join(acs_pids)}")
        print(f"acs.exe is running with PID(s): {', '.join(acs_pids)}")
    else:
        logging.info("acs.exe is not running.")
        print("acs.exe is not running.")

if __name__ == '__main__':
    verify_acs()
