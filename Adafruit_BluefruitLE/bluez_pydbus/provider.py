
from collections import Counter
import sys
import threading
import time

import pydbus

from gi.repository import GObject, GLib


from ..interfaces import Provider

from .adapter import BluezAdapter
from .adapter import _INTERFACE as _ADAPTER_INTERFACE
from .device import BluezDevice

class PyDBusProvider (Provider):
  """BLE provider implementation using bluez DBuz interface and newer and supported "gdbus".
  """
  def __init__ (self):
    # Initialize state for DBus bus, bluez root object,
    # and main loop thread metadata.
    self._bus = None
    self._mainloop = None
    self._bluez = None
    self._user_thread = None
    self._return_code = 0
    self._exception = None

  def initialize (self):
    self._bus = pydbus.SystemBus( )
    GObject.threads_init( )
    self._mainloop = GLib.MainLoop( )
    self._context = self._mainloop.get_context( )
    self._bluez = self._bus.get('org.bluez', '/')


  def run_mainloop_with(self, target, quit_with_loop=True):
      """Start the OS's main loop to process asyncronous BLE events and then
      run the specified target function in a background thread.  Target
      function should be a function that takes no parameters and optionally
      return an integer response code.  When the target function stops
      executing or returns with value then the main loop will be stopped and
      the program will exit with the returned code.

      Note that an OS main loop is required to process asyncronous BLE events
      and this function is provided as a convenience for writing simple tools
      and scripts that don't need to be full-blown GUI applications.  If you
      are writing a GUI application that has a main loop (a GTK glib main loop
      on Linux, or a Cocoa main loop on OSX) then you don't need to call this
      function.
      """
      # Spin up a background thread to run the target code.
      self._user_thread = threading.Thread(target=self._user_thread_main, args=(target,))
      self._user_thread.daemon = True  # Don't let the user thread block exit.
      self._user_thread.start()
      # Spin up a GLib main loop in the main thread to process async BLE events.
      # self._mainloop = GObject.MainLoop()
      # GLib.idle_add(self.iter_context)
      try:
          self._mainloop.run()  # Doesn't return until the mainloop ends.
      except KeyboardInterrupt:
          self._mainloop.quit()
          sys.exit(0)
      # Main loop finished.  Check if an exception occured and throw it,
      # otherwise return the status code from the user code.
      if self._exception is not None:
          # Rethrow exception with its original stack trace following advice from:
          # http://nedbatchelder.com/blog/200711/rethrowing_exceptions_in_python.html
          raise self._exception[1], None, self._exception[2]
      else:
          if quit_with_loop:
              sys.exit(self._return_code)
          else:
              return self._return_code

  def iter_context (self):
    print "iterating context?"
    while 1:
      self._context.iteration(True)
    return True
  def _user_thread_main(self, target):
      """Main entry point for the thread that will run user's code."""
      try:
          # Wait for GLib main loop to start running before starting user code.
          while True:
              if self._mainloop is not None and self._mainloop.is_running():
                  # Main loop is running, we should be ready to make bluez DBus calls.
                  break
              # Main loop isn't running yet, give time back to other threads.
              time.sleep(0)
          # Run user's code.
          self._return_code = target()
          # Assume good result (0 return code) if none is returned.
          if self._return_code is None:
              self._return_code = 0
          # Signal the main loop to exit.
          self._mainloop.quit()
      except Exception as ex:
          # Something went wrong.  Raise the exception on the main thread to
          # exit.
          self._exception = sys.exc_info()
          self._mainloop.quit()

  def clear_cached_data(self):
      """Clear any internally cached BLE device data.  Necessary in some cases
      to prevent issues with stale device data getting cached by the OS.
      """
      # Go through and remove any device that isn't currently connected.
      for device in self.list_devices():
          # Skip any connected device.
          if device.is_connected:
              continue
          # Remove this device.  First get the adapter associated with the device.
          adapter = dbus.Interface(self._bus.get_object('org.bluez', device._adapter),
                                   _ADAPTER_INTERFACE)
          # Now call RemoveDevice on the adapter to remove the device from
          # bluez's DBus hierarchy.
          adapter.RemoveDevice(device._device.object_path)

  def disconnect_devices(self, service_uuids=[]):
      """Disconnect any connected devices that have the specified list of
      service UUIDs.  The default is an empty list which means all devices
      are disconnected.
      """
      service_uuids = Counter(service_uuids)
      for device in self.list_devices():
          # Skip devices that aren't connected.
          if not device.is_connected:
              continue
          device_uuids = Counter(map(lambda x: x.uuid, device.list_services()))
          if device_uuids >= service_uuids:
              # Found a device that has at least the requested services, now
              # disconnect from it.
              device.disconnect()

  def list_adapters(self):
      """Return a list of BLE adapter objects connected to the system."""
      return map(BluezAdapter, self._get_objects('org.bluez.Adapter1'))

  def list_devices(self):
      """Return a list of BLE devices known to the system."""
      return map(BluezDevice, self._get_objects('org.bluez.Device1'))

  def _get_objects(self, interface, parent_path='/org/bluez'):
      """Return a list of all bluez DBus objects that implement the requested
      interface name and are under the specified path.  The default is to
      search devices under the root of all bluez objects.
      """
      # Iterate through all the objects in bluez's DBus hierarchy and return
      # any that implement the requested interface under the specified path.
      parent_path = parent_path.lower()
      objects = []

      for opath, interfaces in self._bluez.GetManagedObjects()[0].iteritems():
          if interface in interfaces.keys() and opath.lower().startswith(parent_path):
              objects.append(self._bus.get('org.bluez', opath))
      return objects

  def _get_objects_by_path(self, paths):
      """Return a list of all bluez DBus objects from the provided list of paths.
      """
      return map(lambda x: self._bus.get('org.bluez', x), paths)

  def _print_tree(self):
      """Print tree of all bluez objects, useful for debugging."""
      # This is based on the bluez sample code get-managed-objects.py.
      objects = self._bluez.GetManagedObjects()
      for path in objects.keys():
          print("[ %s ]" % (path))
          interfaces = objects[path]
          for interface in interfaces.keys():
              if interface in ["org.freedesktop.DBus.Introspectable",
                          "org.freedesktop.DBus.Properties"]:
                  continue
              print("    %s" % (interface))
              properties = interfaces[interface]
              for key in properties.keys():
                  print("      %s = %s" % (key, properties[key]))
