import sys

# https://stackoverflow.com/a/31615605
# for current func name, specify 0 or no argument.
# for name of caller of current func, specify 1.
# for name of caller of caller of current func, specify 2. etc.
currentFuncName = lambda n=0: sys._getframe(n + 1).f_code.co_name

# https://stackoverflow.com/questions/62985573/how-to-get-the-name-of-the-calling-class-in-python
currentClassName = lambda n=0: sys._getframe(n + 1).f_locals["self"].__class__.__name__


# see also https://stackoverflow.com/a/13514318


def bytes_to_hex_string(byte_data, delim=''):
   return ''.join(f'{delim}{byte:02x}' for byte in byte_data)