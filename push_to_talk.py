#!/usr/bin/python
from __future__ import with_statement
import dbus
import gtk
import gnomeapplet
import gobject
import logging
from multiprocessing import Process, Queue
import os
import os.path
import pygtk
import subprocess
from Xlib import display, X
from Xlib.ext import record
from Xlib.protocol import rq

class PushToTalk(gnomeapplet.Applet):
    INTERVAL = 100

    def __init__(self, applet, iid, get_keycode=False):
        self.applet = applet
        self.iid = iid
        self.get_keycode = get_keycode

        self.label = gtk.Label("...")
        self.applet.add(self.label)
        self.applet.show_all()
        self.applet.setup_menu(self.menu_xml, [
                ('Set Key', self.set_key, )
            ], None)

        self.start()

    @classmethod
    def process(cls, pipe, return_pipe, get_keycode):
        interface = AmixerInterface()

        monitor = KeyMonitor(
                interface, 
                pipe,
                return_pipe,
                test=True if get_keycode else False
            )
        monitor.start()

    def read_incoming_pipe(self):
        while not self.pipe.empty():
            data_object = self.pipe.get_nowait()
            data_type = data_object[0]
            data = data_object[1]
            logging.debug("Incoming Data -- %s" % str(data_object))
            if data_type == "MUTED":
                if data == KeyMonitor.UNMUTED:
                    self.set_ui_talk()
                elif data == KeyMonitor.MUTED:
                    self.reset_ui()
        return True

    def reset_ui(self):
        self.label.set_markup("<span>TALK</span>")

    def set_ui_talk(self):
        self.label.set_markup("<span foreground='#FF0000'>TALK</span>")

    def start(self):
        self.pipe = Queue()
        self.return_pipe = Queue()

        p = Process(
                target=self.__class__.process,
                args = (self.pipe, self.return_pipe, self.get_keycode, )
            )
        p.start()

        logging.debug("Process spawned")
        self.label.set_label("TALK")
        gobject.timeout_add(PushToTalk.INTERVAL, self.read_incoming_pipe)

    def set_key(self, *arguments):
        logging.debug("Attempting to set key...")
        self.label.set_markup("<span foreground='#00FF00'>PRESS</span>")
        self.return_pipe.put(("SET", 1, ))
        self.applet.show_all()

    @property
    def menu_xml(self):
        return """<popup name="button3">
            <menuitem name="SetKey" 
                      verb="Set Key" 
                      label="Set Key" 
                      pixtype="stock" 
                      pixname="gtk-preferences"/>
        </popup>
        """

class KeyMonitor(object):
    RELEASE = 0
    PRESS = 1

    UNMUTED = 0
    MUTED = 1

    F1_KEYCODE = 65470
    F12_KEYCODE = 65481
    """
    Heavily borrowed from PyKeyLogger
    """
    def __init__(self, interface, pipe, return_pipe, test = False):
        self.local_dpy = display.Display()
        self.record_dpy = display.Display()
        self.interface = interface
        self.pipe = pipe
        self.return_pipe = return_pipe

        self.configured_keycode = None
        self.state = KeyMonitor.MUTED

        if test == True:
            self.handler = self.print_action
        else:
            self.handler = self.interface_handler

    @property
    def configuration_file(self):
        return os.path.expanduser("~/.push_to_talk_key")

    def get_configured_keycode(self):
        if not self.configured_keycode:
            try:
                with open(self.configuration_file, "r") as infile:
                    keycode = infile.read()
                    self.configured_keycode = int(keycode)
            except:
                self.configured_keycode = KeyMonitor.F12_KEYCODE
        return self.configured_keycode

    def set_configured_keycode(self, keycode):
        logging.info("Setting keycode to %s" % keycode)
        try:
            with open(self.configuration_file, "w") as outfile:
                outfile.write(str(keycode))
                self.configured_keycode = None
            return True
        except Exception as e:
            logging.exception(e)
            return False

    def set_state(self, state):
        if self.state != state:
            self.pipe.put(("MUTED", state, ))
            if state == KeyMonitor.UNMUTED:
                self.interface.unmute()
            elif state == KeyMonitor.MUTED:
                self.interface.mute()
        self.state = state

    def interface_handler(self, key, action):
        configured = self.get_configured_keycode()
        if action == KeyMonitor.PRESS and key == configured:
            self.set_state(KeyMonitor.UNMUTED)
        elif action == KeyMonitor.RELEASE and key == configured:
            self.set_state(KeyMonitor.MUTED)

    def print_action(self, key, action):
        if action == KeyMonitor.RELEASE:
            print "\n%s RELEASE" % key
        elif action == KeyMonitor.PRESS:
            print "\n%s PRESS" % key

    def start(self):
        self.ctx = self.record_dpy.record_create_context(
            0,
            [record.AllClients],
            [{
                    'core_requests': (0, 0),
                    'core_replies': (0, 0),
                    'ext_requests': (0, 0, 0, 0),
                    'ext_replies': (0, 0, 0, 0),
                    'delivered_events': (0, 0),
                    'device_events': (X.KeyPress, X.KeyRelease, ),
                    'errors': (0, 0),
                    'client_started': False,
                    'client_died': False,
            }])

        self.record_dpy.record_enable_context(self.ctx, self.processevents)
        self.record_dpy.record_free_context(self.ctx)

    def processevents(self, reply):
        if reply.category != record.FromServer:
            return
        if reply.client_swapped:
            return
        if not len(reply.data) or ord(reply.data[0]) < 2:
            # not an event
            return
        data = reply.data
        while len(data):
            event, data = rq.EventField(None).parse_binary_value(data, self.record_dpy.display, None, None)
            if event.type == X.KeyPress:
                self.keypressevent(event, KeyMonitor.PRESS)
            elif event.type == X.KeyRelease:
                self.keypressevent(event, KeyMonitor.RELEASE)

    def keypressevent(self, event, action):
        keysym = self.local_dpy.keycode_to_keysym(event.detail, 0)
        if not self.return_pipe.empty():
            logging.debug("Key info %s" % keysym)
            data_object = self.return_pipe.get_nowait()
            data_type = data_object[0]
            logging.debug("Got data %s" % str(data_object))
            if data_type == "SET":
                self.set_configured_keycode(keysym)
            self.handler(keysym, action)
        else:
            self.handler(keysym, action)

class AmixerInterface(object):
    def mute(self):
        subprocess.call([
                'amixer',
                'set',
                'Capture',
                'nocap',
            ])

    def unmute(self):
        subprocess.call([
                'amixer',
                'set',
                'Capture',
                'cap',
            ])

class SkypeInterface(object):
    def __init__(self):
        self.bus = dbus.SessionBus()
        self.configured = False

    def configure(self):
        try:
            logging.debug("Configuring...")
            self.outgoing = self.bus.get_object('com.Skype.API', '/com/Skype')
            self.outgoing_channel = self.outgoing.get_dbus_method('Invoke')
            self.configured = True

            self.start()
            logging.debug("Configured.")
            return False
        except:
            # This happens if Skype is not available.
            return True

    def mute(self):
        self._invoke("MUTE ON")

    def unmute(self):
        self._invoke("MUTE OFF")

    def _invoke(self, message):
        if not self.configured:
            self.configure()
        try:
            self.outgoing_channel(message)
        except:
            self.configured = False

    def start(self):
        self._invoke('NAME PushToTalk')
        self._invoke('PROTOCOL 5')

def push_to_talk_factory(applet, iid, get_keycode=False):
    PushToTalk(applet, iid, get_keycode)
    return gtk.TRUE

pygtk.require('2.0')

gobject.type_register(PushToTalk)

logging.basicConfig(
        filename=os.path.expanduser("~/.push_to_talk.log"),
        level=logging.INFO
    )

if __name__ == "__main__":
    logging.info("Starting via BonoboFactory.")
    gnomeapplet.bonobo_factory(
            "OAFIID:SkypePushToTalk_Factory",
            PushToTalk.__gtype__,
            "hello",
            "0",
            push_to_talk_factory
        )
