# 
# archipelHypervisor.py
# 
# Copyright (C) 2010 Antoine Mercadal <antoine.mercadal@inframonde.eu>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
# 
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Contains TNArchipelVirtualMachines, the entities uses for hypervisor

This provides the possibility to instanciate TNArchipelVirtualMachines
"""
import xmpp
import sys
import socket
import sqlite3
import datetime
import commands
import time
from threading import Thread
from utils import *
from archipelBasicXMPPClient import *
from archipelVirtualMachine import *
import string
import random
import libvirtEventLoop
import libvirt

GROUP_VM                        = "virtualmachines"
GROUP_HYPERVISOR                = "hypervisors"
NS_ARCHIPEL_HYPERVISOR_CONTROL  = "archipel:hypervisor:control"
NS_ARCHIPEL_STATUS_ONLINE       = "Online"


ARCHIPEL_ERROR_CODE_HYPERVISOR_ALLOC        = -9001
ARCHIPEL_ERROR_CODE_HYPERVISOR_FREE         = -9002
ARCHIPEL_ERROR_CODE_HYPERVISOR_ROSTER       = -9003
ARCHIPEL_ERROR_CODE_HYPERVISOR_CLONE        = -9004
ARCHIPEL_ERROR_CODE_HYPERVISOR_IP           = -9005
ARCHIPEL_ERROR_CODE_HYPERVISOR_LIBVIRT_URI  = -9006
ARCHIPEL_ERROR_CODE_HYPERVISOR_MIGRATE      = -9007

class TNThreadedVirtualMachine(Thread):
    """
    this class is used to run L{ArchipelVirtualMachine} main loop
    in a thread.
    """
    def __init__(self, jid, password, hypervisor, configuration, name):
        """
        the contructor of the class
        @type jid: string
        @param jid: the jid of the L{TNArchipelVirtualMachine} 
        @type password: string
        @param password: the password associated to the JID
        """
        Thread.__init__(self)
        self.jid = jid
        self.password = password
        self.xmppvm = TNArchipelVirtualMachine(self.jid, self.password, hypervisor, configuration, name)
        
    
    
    def get_instance(self):
        """
        this method return the current L{TNArchipelVirtualMachine} instance
        @rtype: ArchipelVirtualMachine
        @return: the L{ArchipelVirtualMachine} instance
        """
        return self.xmppvm
    
    
    def run(self):
        """
        overiddes sur super class method. do the L{TNArchipelVirtualMachine} main loop
        """
        try:
            self.xmppvm.connect()
            self.xmppvm.loop()
        except Exception as ex:
            log.error("thread loop exception: %s" % str(ex))
    



        
    



class TNArchipelHypervisor(TNArchipelBasicXMPPClient):
    """
    this class represent an Hypervisor XMPP Capable. This is an XMPP client
    that allows to alloc threaded instance of XMPP Virtual Machine, destroy already
    active XMPP VM, and remember which have been created.
    """       
    
    def __init__(self, jid, password, configuration, name, database_file="./database.sqlite3"):
        """
        this is the constructor of the class.
        
        @type jid: string
        @param jid: the jid of the hypervisor
        @type password: string
        @param password: the password associated to the JID
        @type database_file: string
        @param database_file: the sqlite3 file to store existing VM for persistance
        """
        TNArchipelBasicXMPPClient.__init__(self, jid, password, configuration, name)
        
        self.virtualmachines    = {}
        self.database_file      = database_file
        self.xmppserveraddr     = self.jid.getDomain()
        self.local_libvirt_uri  = self.configuration.get("GLOBAL", "libvirt_uri")
        
        names_file = open(self.configuration.get("HYPERVISOR", "name_generation_file"), 'r')
        self.generated_names = names_file.readlines();
        names_file.close();
        self.number_of_names = len(self.generated_names) - 1
        
        log.info( "server address defined as {0}".format(self.xmppserveraddr))
        
        # libvirt connection
        self.libvirt_connection = libvirt.open(self.local_libvirt_uri)
        if self.libvirt_connection == None:
            log.error( "unable to connect libvirt")
            sys.exit(-42) 
        log.info( "connected to  libvirt")
        
        ## start the run loop
        libvirtEventLoop.virEventLoopPureStart()
        
        # persistance
        self.manage_persistance()
        
        # action on auth
        default_avatar = self.configuration.get("HYPERVISOR", "hypervisor_default_avatar")
        self.register_actions_to_perform_on_auth("set_vcard", {"entity_type": "hypervisor", "avatar_file": default_avatar})
        self.register_actions_to_perform_on_auth("update_presence")
        
    
    
    def update_presence(self, params=None):
        count = len(self.virtualmachines)
        self.change_presence("", NS_ARCHIPEL_STATUS_ONLINE + " (" + str(count)+ ")")
        
    
    
    def register_handler(self):
        """
        this method overrides the defaut register_handler of the super class.
        """
        self.xmppclient.RegisterHandler('iq', self.process_iq, typ=NS_ARCHIPEL_HYPERVISOR_CONTROL)
        TNArchipelBasicXMPPClient.register_handler(self)
    
 
    def manage_persistance(self):
        """
        if the database_file parameter contain a valid populated sqlite3 database,
        this method will recreate all the old L{TNArchipelVirtualMachine}. if not, it will create a 
        blank database file.
        """
        log.info( "opening database file {0}".format(self.database_file))
        self.database = sqlite3.connect(self.database_file, check_same_thread=False)
        
        log.info( "populating database if not exists")
        
        self.database.execute("create table if not exists virtualmachines (jid text, password text, creation_date date, comment text, name text)")
            
        c = self.database.cursor()
        c.execute("select * from virtualmachines")
        for vm in c:
            jid, password, date, comment, name = vm
            vm = self.create_threaded_vm(xmpp.JID(jid), password, name)
            # add hypervisor in the VM roster. This allow to manually add vm into the database
            # and during restart, being able to delete it from the GUI
            # vm.register_actions_to_perform_on_auth("add_jid", self.jid.getStripped(), oneshot=True)
            self.virtualmachines[vm.jid.getNode()] = vm.get_instance()
    
        
    def create_threaded_vm(self, jid, password, name):
        """
        this method creates a threaded L{TNArchipelVirtualMachine}, start it and return the Thread instance
        @type jid: string
        @param jid: the JID of the L{TNArchipelVirtualMachine}
        @type password: string
        @param password: the password associated to the JID
        @rtype: L{TNThreadedVirtualMachine}
        @return: a L{TNThreadedVirtualMachine} instance of the virtual machine
        """
        vm = TNThreadedVirtualMachine(jid, password, self, self.configuration, name)
        #vm.daemon = True
        vm.start()
        return vm    
    
    
    def generate_name(self):
        return self.generated_names[random.randint(0, self.number_of_names)].replace("\n", "")
    
    
    
    ######################################################################################################
    ### XMPP Processing
    ######################################################################################################
    
    def process_iq(self, conn, iq):
        """
        this method is invoked when a NS_ARCHIPEL_HYPERVISOR_CONTROL IQ is received.
        
        it understands IQ of type:
            - alloc
            - free
            
        @type conn: xmpp.Dispatcher
        @param conn: ths instance of the current connection that send the stanza
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        """
        try:
            action = iq.getTag("query").getTag("archipel").getAttr("action")
            log.info( "IQ RECEIVED: from: %s, type: %s, namespace: %s, action: %s" % (iq.getFrom(), iq.getType(), iq.getQueryNS(), action))
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, NS_ARCHIPEL_ERROR_QUERY_NOT_WELL_FORMED)
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed
            
        if action == "alloc":
            reply = self.iq_alloc(iq)
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed
            
        elif action == "free":
            reply = self.iq_free(iq)
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed
            
        elif action == "rostervm":
            reply = self.iq_roster(iq)
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed
            
        elif action == "clone":
            reply = self.iq_clone(iq)
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed
        
        elif action == "migrate":
            reply = self.iq_migrate(iq)
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed
        
        elif action == "ip":
            reply = self.iq_ip(iq)
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed
            
        elif action == "uri":
            reply = self.iq_libvirt_uri(iq)
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed
    
    
    
    
    ######################################################################################################
    ###  Hypervisor controls
    ######################################################################################################
    
    def alloc(self, requester, requested_name=None, description="", requested_uuid=None, requested_password=None):
        """
        Alloc a new XMPP entity
        """
        if requested_uuid:
            vmuuid = requested_uuid
        else:
            vmuuid  = str(uuid.uuid1())
        
        if requested_password:
            vm_password = requested_password
        else:
            vm_password = ''.join([random.choice(string.letters + string.digits) for i in range(self.configuration.getint("VIRTUALMACHINE", "xmpp_password_size"))])
        
        vm_jid      = xmpp.JID(node=vmuuid.lower(), domain=self.xmppserveraddr.lower())
        
        log.info( "adding the xmpp vm %s to my roster" % (str(vm_jid)))
        self.add_jid(vm_jid, [GROUP_VM])
        
        if not requested_name:
            name = self.generate_name()
        else:
            name = requested_name
        
        log.info("starting xmpp threaded virtual machine")
        vm = self.create_threaded_vm(vm_jid, vm_password, name).get_instance()
        
        log.info( "adding the requesting controller %s to the VM's roster" % (str(requester)))
        vm.register_actions_to_perform_on_auth("add_jid", requester, persistant=False)
        
        log.info( "registering the new VM in hypervisor's memory")
        self.database.execute("insert into virtualmachines values(?,?,?,?,?)", (str(vm_jid), vm_password, datetime.datetime.now(), description, name))
        self.database.commit()
        self.virtualmachines[vmuuid] = vm
        
        self.update_presence()
        log.info( "XMPP Virtual Machine instance sucessfully initialized")
        
        return vm
        
    
    
    def free(self, jid, keep_folder=False, keep_account=False):
        uuid    = jid.getNode()
        vm      = self.virtualmachines[uuid]
        
        try:
            if vm.domain and (vm.domain.info()[0] == 1 or vm.domain.info()[0] == 2 or vm.domain.info()[0] == 3):
                vm.domain.destroy()
                vm.domain.undefine()
        except:
            pass
        
        log.info( "removing the xmpp vm %s from my roster" % (str(jid)))
        self.remove_jid(jid)
        
        if not keep_folder:
            log.info( "removing the vm drive directory")
            vm.remove_folder()
        
        log.info( "unregistering the VM from hypervisor's database")
        self.database.execute("delete from virtualmachines where jid='{0}'".format(jid))
        self.database.commit()
        
        try:
            del self.virtualmachines[uuid]
        except:
            pass
        
        if not keep_account:
            log.info( "unregistering vm from jabber server ".format(jid))
            vm._inband_unregistration()
        else:
            vm.disconnect()
        self.update_presence()
    
    
    def clone(self, uuid, requester):
        xmppvm      = self.virtualmachines[uuid]
        xmldesc     = xmppvm.definition;
        
        if not xmldesc:
            raise Exception('The mother vm has to be defined to be cloned')
        
        dominfo = xmppvm.domain.info();
        if not (dominfo[0] == VIR_DOMAIN_SHUTOFF or dominfo[0] == VIR_DOMAIN_SHUTDOWN):
            raise Exception('The mother vm has to be stopped to be cloned')
        
        name = "%s (clone)" % xmppvm.name;
        newvm = self.alloc(requester, requested_name=name);
        newvm.register_actions_to_perform_on_auth("clone", {"definition": xmldesc, "path": xmppvm.folder, "baseuuid": uuid}, persistant=False)
    
    
    
    ######################################################################################################
    ###  Hypervisor IQs
    ######################################################################################################
    
    def iq_alloc(self, iq):
        """
        this method creates a threaded L{TNArchipelVirtualMachine} with UUID given 
        as paylood in IQ and register the hypervisor and the iq sender in 
        the VM's roster
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            try:
                requested_name = iq.getTag("query").getTag("archipel").getAttr("name")
            except:
                requested_name = None
                
            try:
                requested_uuid = iq.getTag("query").getTag("archipel").getAttr("uuid")
            except:
                requested_uuid = None
            
            try:
                requested_password = iq.getTag("query").getTag("archipel").getAttr("password")
            except:
                requested_password = None
            
            if self.virtualmachines.has_key(requested_uuid):
                log.info("no needs to alloc vm. already in memory")
                return
            
            vm = self.alloc(iq.getFrom(), requested_name=requested_name, requested_uuid=requested_uuid, requested_password=requested_password)
            
            reply   = iq.buildReply("result")
            payload = xmpp.Node("virtualmachine", attrs={"jid": str(vm.jid)})
            reply.setQueryPayload([payload])
            
            self.push_change("hypervisor", "alloc", excludedgroups=[GROUP_VM]);
            self.shout("virtualmachine", "A new Archipel Virtual Machine has been created by %s with uuid %s" % (iq.getFrom(), vm.uuid), excludedgroups=[GROUP_VM])
        except libvirt.libvirtError as ex:
            reply = build_error_iq(self, ex, iq, ex.get_error_code(), ns=NS_LIBVIRT_GENERIC_ERROR)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_ALLOC)
            
        return reply
    
    
    def iq_free(self, iq):
        """
        this method destroy a threaded L{TNArchipelVirtualMachine} with UUID given 
        as paylood in IQ and remove it from the hypervisor roster
        
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        reply = iq.buildReply("result")
        
        try:
            vm_jid      = xmpp.JID(jid=iq.getTag("query").getTag("archipel").getAttr("jid"))
            domain_uuid = vm_jid.getNode()
            
            keep_folder = False
            try: 
                keep = iq.getTag("query").getTag("archipel").getAttr("keepfolder");
                if keep == "1":
                    keep_folder = True
            except:
                pass
                
            keep_account = False
            try: 
                keep = iq.getTag("query").getTag("archipel").getAttr("keepfolder");
                if keep == "1":
                    keep_account = True
            except:
                pass
                    
            
            self.free(vm_jid, keep_folder=keep_folder, keep_account=keep_account)
            
            reply.setQueryPayload([xmpp.Node(tag="virtualmachine", attrs={"jid": str(vm_jid)})])
            log.info( "XMPP Virtual Machine instance sucessfully destroyed")
            self.push_change("hypervisor", "free", excludedgroups=[GROUP_VM]);
            self.shout("virtualmachine", "The Archipel Virtual Machine %s has been destroyed by %s" % (domain_uuid, iq.getFrom()), excludedgroups=[GROUP_VM])
        except libvirt.libvirtError as ex:
            reply = build_error_iq(self, ex, iq, ex.get_error_code(), ns=NS_LIBVIRT_GENERIC_ERROR)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_FREE)
        
        return reply
    
    
    def iq_clone(self, iq):
        """
        alloc a virtual as a clone of another

        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ

        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply       = iq.buildReply("result")
            vmjid       = xmpp.JID(jid=iq.getTag("query").getTag("archipel").getAttr("jid"))
            vmuuid      = vmjid.getNode();
            
            self.clone(vmuuid, iq.getFrom())
            
            self.push_change("hypervisor", "clone", excludedgroups=[GROUP_VM]);
            self.shout("virtualmachine", "The Archipel Virtual Machine %s has been cloned by %s" % (vmuuid, iq.getFrom()), excludedgroups=[GROUP_VM])
            
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_CLONE)
        return reply
    
    
    def iq_roster(self, iq):
        """
        send the hypervisor roster content
        
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            reply = iq.buildReply("result")
            nodes = []
            for uuid, vm in self.virtualmachines.iteritems():#self.roster.getItems():
                n = xmpp.Node("item")
                n.addData(vm.jid)
                nodes.append(n)
            reply.setQueryPayload(nodes)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_ROSTER)
        return reply
    
        
    def iq_ip(self, iq):
        """
        send the hypervisor IP address
        
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            reply = iq.buildReply("result")
            reply.getTag("query").addChild(name="ip", payload=self.ipaddr);
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_IP)
        return reply

    
    
    def iq_libvirt_uri(self, iq):
        """
        send the hypervisor IP address

        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            network_libvirt_uri = self.local_libvirt_uri.replace("///", "//%s/" % self.ipaddr)
            reply = iq.buildReply("result")
            reply.getTag("query").addChild(name="uri", payload=network_libvirt_uri);
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_LIBVIRT_URI)
        return reply


    