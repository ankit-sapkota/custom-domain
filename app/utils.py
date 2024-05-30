import os, errno
import string
import random

def generate_random_string(length=32):
    letters = string.ascii_letters + string.digits
    return "bettercollected_" + ''.join(random.choice(letters) for i in range(length))

def silent_remove_file(filename):
    try:
        os.remove(filename)
    except OSError as e: 
        if e.errno != errno.ENOENT: 
            raise 