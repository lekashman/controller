#!/usr/bin/env python3
'''
Host-Side Setup Routines for KLL
'''

# Copyright (C) 2016 by Jacob Alexander
#
# This file is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This file is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this file.  If not, see <http://www.gnu.org/licenses/>.

### Imports ###

import argparse
import ctypes
import importlib
import inspect
import os
import pty
import sys
import termios

from ctypes import CFUNCTYPE, c_int, c_char_p

import serial



### Decorators ###

## Print Decorator Variables
ERROR = '\033[5;1;31mERROR\033[0m:'
WARNING = '\033[5;1;33mWARNING\033[0m:'


## Python Text Formatting Fixer...
##  Because the creators of Python are averse to proper capitalization.
textFormatter_lookup = {
	"usage: "            : "Usage: ",
	"optional arguments" : "Optional Arguments",
}

def textFormatter_gettext( s ):
	return textFormatter_lookup.get( s, s )

argparse._ = textFormatter_gettext



### Variables ###

callback_ptrs = []



### Classes ###

class Control:
	'''
	Handles general control of the libkiibohd host setup
	'''
	def __init__( self, scan_module, output_module, libkiibohd_path ):
		'''
		Initializes control object

		@param scan_module:   Path to ScanModule python script
		@param output_module: Path to OutputModule python script
		'''
		self.scan_module = scan_module
		self.output_module = output_module

		self.CTYPE_callback = None
		self.CTYPE_callback_ref = None
		self.serial = None
		self.serial_buf = ""

		# Provide reference to this class when running callback
		# Due to memory schemes, we have to use a standard Python function and not a method
		# or event a factory function (my experiments failed miserably on multiple calls)
		global control
		control = self

		# Import Scan and Output modules
		global scan
		global output
		spec = importlib.util.spec_from_file_location( "Scan", self.scan_module )
		scan = importlib.util.module_from_spec( spec )
		spec.loader.exec_module( scan )

		spec = importlib.util.spec_from_file_location( "Output", self.output_module )
		output = importlib.util.module_from_spec( spec )
		spec.loader.exec_module( output )

		# Build command and callback dictionaries
		self.build_command_list()
		self.build_callback_list()

		# Set references in Scan and Output modules
		scan.control = self
		output.control = self

		# Import libkiibohd
		global kiibohd
		try:
			kiibohd = ctypes.CDLL( libkiibohd_path )
		except Exception as err:
			print( "{0} Could not open -> {1}".format( ERROR, libkiibohd_path ) )
			print( err )
			sys.exit( 1 )

		# Register Callback
		self.callback_setup()

	def build_command_list( self ):
		'''
		Builds dictionary of commands that can be called
		'''
		# Merges both dictionaries together (Python 3.5+)
		self.command_dict = {
			**get_method_dict( scan.Commands() ),
			**get_method_dict( output.Commands() ),
		}

	def build_callback_list( self ):
		'''
		Builds dictionary of callbacks that libkiibohd.so may call
		'''
		# Merges both dictionaries together (Python 3.5+)
		self.callback_dict = {
			**get_method_dict( scan.Callbacks() ),
			**get_method_dict( output.Callbacks() ),
		}

	def callback_setup( self ):
		'''
		Setup callback
		'''
		self.CTYPE_callback = CFUNCTYPE( c_int, c_char_p, c_char_p )
		try:
			self.CTYPE_callback_ref = kiibohd.Host_register_callback( self.CTYPE_callback( callback ) )
		except Exception as err:
			print( "{0} Could not register libkiibohd callback function".format( ERROR ) )
			print( err )
			sys.exit( 1 )

	def process_args( self ):
		'''
		Process command line arguments
		'''
		# Setup argument processor
		parser = argparse.ArgumentParser(
			usage="%(prog)s [options..]",
			description="Kiibohd Host KLL Implementation",
			epilog="Example: {0} TODO".format( os.path.basename( sys.argv[0] ) ),
			formatter_class=argparse.RawTextHelpFormatter,
			add_help=False,
		)

		# Optional Arguments
		parser.add_argument( '-h', '--help',
			action="help",
			help="This message."
		)
		parser.add_argument( '-c', '--cli',
			action="store_true",
			help="Enables virtual serial port interface."
		)
		parser.add_argument( '-d', '--debug',
			action="store_true",
			help="Enable debug mode."
		)
		parser.add_argument( '-t', '--test',
			action="store_true",
			help="Run small test function to validate that Python callback interface is working."
		)

		# Process Arguments
		args = parser.parse_args()

		# Enable debug mode
		self.debug = args.debug
		scan.debug = args.debug
		output.debug = args.debug

		# Enable virtual serial port
		if args.cli:
			print("Enabling Virtual Serial Port")
			self.virtual_serialport_setup()

		# Run test if requested, then exit
		if args.test:
			print("libkiibohd.so - Callback Test")
			val = kiibohd.Host_callback_test()
			print("Return Value:", val )
			sys.exit( 0 )

		return args

	def process( self ):
		'''
		Run main commands
		'''
		# Initialize kiibohd
		kiibohd.Host_init()

		# Run cli loop if available
		while self.serial is not None:
			value = os.read( self.serial_master, 1 ).decode('utf-8')
			self.serial_buf += value

			# Debug output
			if self.debug:
				print( value, end='' )
				sys.stdout.flush()

			# Check if any cli commands need to be processed
			kiibohd.Host_cli_process()

	def virtual_serialport_setup( self ):
		'''
		Setup virtual serial port
		'''
		# Open pty device, and disable echo (to simulate microcontroller virtual serial port)
		self.serial_master, self.serial_slave = pty.openpty()
		settings = termios.tcgetattr( self.serial_master )
		settings[3] = settings[3] & ~termios.ECHO
		termios.tcsetattr( self.serial_master, termios.TCSADRAIN, settings )

		# Setup ttyname
		self.serial_name = os.ttyname( self.serial_slave )
		print( self.serial_name )

		# Setup serial interface
		self.serial = self.serial_master



### Functions ###

def get_method_dict( obj ):
	'''
	Given an object return a dictionary of function name:function mappings
	'''
	output = {}
	for name, function in inspect.getmembers( obj, predicate=inspect.ismethod ):
		output[ name ] = function
	return output


def callback( command, args ):
	'''
	libkiibohd callback function
	'''
	if control.debug:
		print( "Callback:", command, args )

	# Lookup function in callback dictionary
	# Every function must taken a single argument
	# Must convert byte string to utf-8 first
	ret = control.callback_dict[ command.decode('utf-8') ]( args.decode('utf-8') )

	# XXX
	# Refresh callback pointer
	# For some reason, either garbage collection, or something else, the pointer becomes stale in certain situations
	# Usually when calling different library functions
	# This just refreshes the pointer (shouldn't be necessary, but it works...) -Jacob
	control.CTYPE_callback_ref = kiibohd.Host_register_callback( control.CTYPE_callback( callback ) )

	# If returning None (default), change out to 1, C default
	return ret is None and 1 or ret



### Main Entry Point ###

if __name__ == '__main__':
	print( "{0} Do not call directly.".format( ERROR ) )
	sys.exit( 1 )

