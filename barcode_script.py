import sys
import select
import threading
import datetime
import time
import RPi.GPIO as GPIO
import requests
from PCF8574 import PCF8574_GPIO 
from Adafruit_LCD1602 import Adafruit_CharLCD


device_id = "BP12" #Change based on machine number
serverUrl = 'http://192.168.1.86:3000' #Change based on IP address of server
activitiesToTrack = ['break', 'downtime', 'setup'] #Change based on activities to track. First 3 activities will be assigned to buttons A, B, C respectively

PCF8574_address = 0x27  # I2C address of the PCF8574 chip.
PCF8574A_address = 0x3F  # I2C address of the PCF8574A chip.
# Create PCF8574 GPIO adapter.
try:
    mcp = PCF8574_GPIO(PCF8574_address)
except Exception as e1:
    try:
        mcp = PCF8574_GPIO(PCF8574A_address)
    except Exception as e2:
        print('I2C Address Error!')
        exit(1)

lcd = Adafruit_CharLCD(pin_rs=0, pin_e=2, pins_db=[4, 5, 6, 7], GPIO=mcp)
mcp.output(3, 1)
lcd.begin(16, 2)
#NOTE: END DISABLE IN DEVELOPMENT

#set variables for gpio keypad
LINES = [6, 13, 19, 26]
COLS = [12, 16, 20, 21]

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

# Make sure to configure the input pins to use the internal pull-down resistors
for line in LINES:
    GPIO.setup(line, GPIO.OUT)
for col in COLS:
    GPIO.setup(col, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

# Keypad mapping
KEYS = [
    ["1", "2", "3", "A"],
    ["4", "5", "6", "B"],
    ["7", "8", "9", "C"],
    ["*", "0", "#", "D"]
]

order_qty = None
state = 'idle'
times = {activity: [] for activity in (activitiesToTrack + ['order'])}
times['order'].append({'start': None, 'end': None})
order_number = None

def await_input():
    """
    Waits for input from either a barcode scanner (keyboard) or a keypad.
    If numeric keys are pressed, outputs the value of `order_number`.
    """

    last_key = None

    print("Awaiting input... (Press Enter for scanner input or '#' for keypad input)")

    while True:
        # Check for keypad input
        detected_key = None
        for row_idx, line in enumerate(LINES):
            GPIO.output(line, GPIO.HIGH)
            for col_idx, col in enumerate(COLS):
                if GPIO.input(col) == GPIO.HIGH:
                    detected_key = KEYS[row_idx][col_idx]
            GPIO.output(line, GPIO.LOW)

        if detected_key != last_key:
            if last_key is not None and detected_key is None:  # Key release
                if last_key in "ABCD":  # Handle special keys
                    mapping = {"A": activitiesToTrack[0], "B": activitiesToTrack[1], "C": activitiesToTrack[2], "D": "submit"}
                    return mapping[last_key]
                elif last_key.isdigit() and order_number is not None:  # Handle numeric input
                    return order_number
            last_key = detected_key

        # Check for keyboard (scanner) input
        if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
            scanner_input = sys.stdin.readline().strip()
            if scanner_input:  # End scanner input
                return scanner_input

        time.sleep(0.1)  # Debounce delay
def keypad_input(prompt):
    """
    Reads keys from the keypad until '#' is pressed.
    Detects key presses only after the key is released.
    '*' acts as backspace.
    """
    update_lcd(prompt, "")
    buffer = ""
    last_key = None

    while True:
        detected_key = None

        # Scan the keypad for a key press
        for row_idx, line in enumerate(LINES):
            GPIO.output(line, GPIO.HIGH)
            for col_idx, col in enumerate(COLS):
                if GPIO.input(col) == GPIO.HIGH:
                    detected_key = KEYS[row_idx][col_idx]
            GPIO.output(line, GPIO.LOW)

        # Handle key press detection only when key state changes (press & release)
        if detected_key != last_key:
            if last_key is not None and detected_key is None:  # Key release
                if last_key == "#":  # End input
                    return buffer
                elif last_key == "*":  # Backspace
                    buffer = buffer[:-1]
                else:  # Add to buffer
                    buffer += last_key
                update_lcd(prompt, buffer)
            last_key = detected_key

        time.sleep(0.1)  # Debounce delay

def update_lcd(line1, line2):
    """Update the LCD screen with the given text. Max 16 characters per line.

    Args:
        line1 (_str_): Upper Line Text
        line2 (_str_): Lower Line Text
    """
    print(f"Line 1: {line1}")
    print(f"Line 2: {line2}")
    line1 = line1[:16]
    line2 = line2[:16]
    
    lcd.clear()
    lcd.setCursor(0, 0)
    lcd.message(line1)
    lcd.setCursor(0, 1)
    lcd.message(line2)
    
    return 0

def send_heartbeat():
    endpoint = serverUrl + '/heartbeat'
    try:
        result = requests.get(endpoint, timeout=5, json={
            'device_id': str(device_id),
        })
        return True
    except Exception as e:
        print(f"Failed to send heartbeat: {e}")
        return False

# Check for connection on startup:
update_lcd("CONNECTING", "TO SERVER...")
def establish_server_connection():
    """check for server connection on startup and attempt to reconnect if connection fails
    """
    while True:
        endpoint = serverUrl + '/client_reboot'
        try:
            result = requests.get(endpoint, timeout=10, json={
                'device_id': str(device_id),
            })
            break
        except Exception as e:
            print(f"Failed to send msg: {e}")
        
        print('Connection Failed!, attempting reconnect in 15 seconds')
        time.sleep(15)
establish_server_connection()

def heartbeat_loop(interval=300):
    while True:
        send_heartbeat()
        time.sleep(interval)

# Start the heartbeat loop in a separate thread
heartbeat_thread = threading.Thread(target=heartbeat_loop, args=(300,))
heartbeat_thread.daemon = True
heartbeat_thread.start()

# Send completed orders to the server
def submit_order():
    update_lcd("Submitting", "Order...")
    endpoint = serverUrl + '/log'
    json = {
        'device_id': str(device_id),
        'order_number': str(order_number),
        'start_time': str(times['order'][0]['start']),
        'end_time': str(times['order'][0]['end']),
        'qty': str(order_qty),
    }
    timesJSON = {}
    for activity in activitiesToTrack:  
        arr = [{'start': str(entry['start']), 'end': str(entry['end'])} for entry in times[activity]]
        timesJSON[activity] = arr
    json['times'] = timesJSON
    try:
        result = requests.post(endpoint, timeout=15, json=json)
        print('Server host response: ' + result.text)
        update_lcd("Order Submitted", "Successfully")
        time.sleep(2)
        return True
    except Exception as e:
        update_lcd("Failed to Submit", "Order")
        time.sleep(2)
        print(e)
        return False

def reset_state():
    global order_number, state, times, order_qty
    order_qty = None
    order_number = None
    state = 'idle'
    times = {activity: [] for activity in (activitiesToTrack + ['order'])}
    times['order'].append({'start': None, 'end': None})
    return

# Input handler to avoid invalid actions and ensure robustness
try:
    update_lcd("Status: " + state.upper(), f"Order: {order_number or 'None'}")
    while True:
        action = await_input()
        if action in activitiesToTrack:
            if state in ('idle', 'running'):
                if state == 'idle':
                    times['order'][0]['start'] = datetime.datetime.now()
                state = action
                times[action].append({'start': datetime.datetime.now()})
            elif state == action:
                times[action][-1]['end'] = datetime.datetime.now()
                if order_number is not None:
                    state = 'running'
                else:
                    state = 'idle'
                    #TODO: submit to server
                    print('submitting')
                    times['order'][0]['end'] = times[action][-1]['end']
                    submit_order()
                    reset_state()
            else:
                # end the current activity and start the new one
                times[state][-1]['end'] = datetime.datetime.now()
                state = action
                times[action].append({'start': datetime.datetime.now()})
        elif action == 'submit':
            if state == 'idle':
                continue
            if state in activitiesToTrack:
                times[state][-1]['end'] = datetime.datetime.now()
            print('submitting')
            if order_number is None:
                times['order'][0]['end'] = times[state][-1]['end']
            else:
                times['order'][0]['end'] = datetime.datetime.now()
            submit_order()
            reset_state()
        elif action == 'cancel':
            if state == 'running':
                reset_state()
            elif state == 'idle':
                print("No action to cancel")               
            elif state in activitiesToTrack:
                if order_number is not None:
                    times[state][-1]['end'] = None
                    times[state].pop()
                    state = 'running'
                else:
                    reset_state()
        else:
            order_number = action
            if state == 'idle':
                state = 'running'
                times['order'][0]['start'] = datetime.datetime.now()
            order_qty = keypad_input("Enter Order Qty")
        
        if(state == 'running'):
            update_lcd(order_number, f"Qty: {order_qty or 'None'}")
        else: 
            update_lcd("Status: " + state.upper(), f"Order: {order_number or 'None'}")

except KeyboardInterrupt:
    print("Process interrupted. Cleaning up and exiting...")
    lcd.clear()