# -*- coding: utf-8 -*-
"""
Created on Thu Mar  5 13:27:23 2020

@author: jido
"""

from StorageManager import CStorage,CStorageMongoDB
from multiprocessing.connection import Listener
from multiprocessing.managers import BaseManager  
import multiprocessing as mp
import argparse
from StellarLog.StellarLog import CLog,CDirectoryConfig,datetime
import yaml 
import ReturnCode
import diskcache
from MiddleWare import calNextStartDate
import time

class CCommandDict:
    
    def __init__(self):
        self.command = dict()

    def __call__(self,key,func):
        if(callable(func)):
            self.command[key] = func
        else:
            raise ValueError("input should be a function")
            
    def __getitem__(self,key):
        return self.command[key]
    
oCommandDict = CCommandDict()
oCommandDict('nextDate',calNextStartDate)


class CConfigByYaml:
    
    def __init__(self,filePath):
        self.content = self._readYaml(filePath)
        
    def _readYaml(self,filePath):
        ans = None
        with open(filePath,'r') as stream:
            try:
                ans = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                print(exc)
        return ans
    
    def __getitem__(self,attr):
        return self.content[attr]
    
    def getConfigValues(self,attrs=None):
        if(attrs == None):
            return self.content
        else:
            ans = dict()
            for attr in attrs:
                ans[attr] = self.content[attr]
            return ans
        
def parse_args():
  """
  Parse input arguments
  """
  parser = argparse.ArgumentParser(description='Train a Fast R-CNN network')
  parser.add_argument('--name', dest='name',
                      help='name',
                      default='test', type=str)
  parser.add_argument('--dbPath', dest='dbPath',
                    help='database path',
                    default='mongodb://localhost:27017/', type=str)
  parser.add_argument('--logFlag', dest='logFlag',
                    help='if include logging',
                    default=True, type=bool)

  args = parser.parse_args()
  return args

class CServer:
    
    def __init__(self,address:tuple):
        self.address = address
        self.listener = Listener(self.address, authkey=b'secret password')
        self.conn = None
        
    def start(self):
        self.conn = self.listener.accept()
    
    def recv(self):
        return self.conn.recv()
    
    def send(self,content):
        return self.conn.send(content)
    
    def close(self):
        return self.conn.close()
    
    def poll(self,timeout=1):
        return self.conn.poll(timeout=timeout)
    
    def getConnection(self):
        return self.conn
    
class CCrsProcManager(BaseManager):
    pass

CCrsProcManager.register('server', CServer)#regested class in Manager can only use the class's method but not attribute
#https://docs.python.org/3.6/library/multiprocessing.html#multiprocessing.managers.BaseManager.register

CCrsProcManager.register('oLog', CLog)


## Multiprocessing Functions

def listenReq(oServer:CServer,oCache:mp.Queue): # old version
    msg = oServer.recv()
    oCache.put(msg)
    return 0

def sendRes(oServer:CServer,msg): # old version
    oServer.send(msg+'multiprocess')
    return 0

def onCallRecvDaemon(oServer:CServer,oInstrCache:mp.Queue,oRecvCache:mp.Queue,oLog:CLog=None):
    while True:
        instr = ''
        recvMsg = ''
        otherEndCloseFlag = True
        
        if(oServer.poll(3) != False):# poll will return true when the other end of the pipe is closed
#            print("onCallRecv: Waiting for msg",oServer.poll())
            try:
                recvMsg = oServer.recv()
            except:
                otherEndCloseFlag = True #!!!important
                recvMsg = ''
            else:
                print('onCallRecv_recv_msg: ',recvMsg)
                if(oLog != None):
                    oLog.safeRecordTime('onCallRecv_recv_msg: '+recvMsg)
                err = oRecvCache.put(recvMsg)
#                print(err)
                otherEndCloseFlag = False
                if(recvMsg == 'quitBusyMode'):
                    return err
        
        if(oServer.poll(0) == False or otherEndCloseFlag):
            try:
                instr = oInstrCache.get(block = False)
            except:
                instr = ''
            else:
                if (instr == 'close'):
                    print('onCallRecv: recv inst: ' + instr)
                    if(oLog != None):
                        oLog.safeRecordTime('onCallRecv_recv_inst: ' + instr)
                    oInstrCache.put('onCallRecv_Close')
                    return
                else:
                    pass
        time.sleep(1)
        
    return 0

def onCallSendDaemon(oServer:CServer,oInstrCache:mp.Queue,oSendCache:mp.Queue,oLog:CLog=None):
    while True:
        instr = ''
        sendMsg = ''
        
        try:
            sendMsg = oSendCache.get(block = False)
        except:
            sendMsg = ''
        else:
            oServer.send(sendMsg)
            print('onCallSend_send_msg: ', sendMsg)
            if(oLog != None):
                oLog.safeRecordTime('onCallSend_send_msg: '+sendMsg)
        
        
        if(oSendCache.empty() == True):
            try:
                instr = oInstrCache.get(block = False)
            except:
                instr = ''
            else:
                if (instr == 'close'):
                    print('onCallSend: recv inst: ' + instr)
                    if(oLog != None):
                        oLog.safeRecordTime('onCallSend_recv_inst: ' + instr)
                    oInstrCache.put('onCallSend_Close')
                    return
                else:
                    pass
        time.sleep(1)
        
    return 0

class CKnowledge:
    
    def __init__(self,name, dbPath:str,logFlag = False):
        self.name = name + '_knowledge'
        self._oStorageManager:CStorage = CStorageMongoDB(name,dbPath)
        oConfig = CConfigByYaml('./conf/ConfigAttributes.yml')['KnowledgeManager']
        self.address = (oConfig['address'], oConfig['port'])
        self.oCrsProcManager = CCrsProcManager()
        self.oServer = None
        self.oRecvCache = mp.Queue()
        self.oSendCache = mp.Queue()
        self.oInstrRecvCache = mp.Queue()
        self.oInstrSendCache = mp.Queue()
        self.prcRecv = None
        self.prcSend = None
        self.logFlag = logFlag
        self.oLog = None
        global oCommandDict
        self.oCommandDict = oCommandDict
        oDir = self.configDir()
        self.oAgentCache = diskcache.Cache(oDir['cacheAgentFolder'])
        print(self.logFlag)
        
    def configDir(self):
        dir_list = ['cacheAgentFolder']
        oDir = CDirectoryConfig(dir_list,'./conf/filesDirectory.conf')
        oDir.checkFolders()
        return oDir
        
    def configLogger(self):
        dir_list = ['Log']
        oDir = CDirectoryConfig(dir_list,'./conf/filesDirectory.conf')
        oDir.checkFolders()
        self.oLog = self.oCrsProcManager.oLog(oDir['Log'],'CKnowledge'+str(datetime.datetime.now().date()))
    
    def startServer(self):
        self.oCrsProcManager.start()
        if(self.logFlag == True):
            self.configLogger()
        self.oServer = self.oCrsProcManager.server(self.address)
        self.oServer.start()
        oConn = self.oServer.getConnection()
        print(oConn.fileno())
        print('server start')
        if self.logFlag: 
            self.oLog.safeRecordTime('server_start')
        self.prcRecv = mp.Process(target = onCallRecvDaemon, 
                                  args=[self.oServer, self.oInstrRecvCache,self.oRecvCache,self.oLog])
        
        self.prcSend = mp.Process(target = onCallSendDaemon,
                                  args=[self.oServer, self.oInstrSendCache,self.oSendCache,self.oLog])
        while (True):
            recvMsg = self.oServer.recv()
            if(recvMsg == 'busyMode'):
                err = self.busyMode()
                if(err == 0):
                    break
            elif(recvMsg == 'close'):
                break
            elif(recvMsg in self.oCommandDict.command.keys()):
                    print('start handling service request: ' + recvMsg)
                    self.handleRegisteredService(recvMsg)
                    print('finish handling service request: ' + recvMsg)
            else:
                print('recv msg: ',recvMsg)
            
        self.handleCloseMsg()
        return 0
        
    def busyMode(self):
        print('enter busy mode')
        if self.logFlag: 
            self.oLog.safeRecordTime('enter busy mode')
            
        self.prcRecv.start()
        self.prcSend.start()
        
        while True:
            recvMsg = ''
            try:
                recvMsg = self.oRecvCache.get(block=False)
            except:
                recvMsg = ''
            else:
#                print(self.oCommandDict.command.keys())
                if(recvMsg == 'close'):
                    self.closeBusyMode()
                    return 0
                elif(recvMsg in self.oCommandDict.command.keys()):
                    print('start handling service request: ' + recvMsg)
                    self.handleRegisteredService_busyMode(recvMsg)
                    print('finish handling service request: ' + recvMsg)
                elif(recvMsg == 'quitBusyMode'):
                    self.closeBusyMode()
                    return 1
                    
        return 0
    
    def handleRegisteredService(self,key):
        result= ''
        if(self._oStorageManager.checkExist()):
            result = self.oCommandDict[key](self._oStorageManager.db)
        else:
            result = 'database: ' + self._oStorageManager.dbName + ' is not existed'
        self.oAgentCache[key] = result
        self.oServer.send(key)
        print('send_msg: ',key)
        if self.logFlag: 
            self.oLog.safeRecordTime('send_msg: ' + key)
    
    def handleRegisteredService_busyMode(self,key):
        result= ''
        if(self._oStorageManager.checkExist()):
            result = self.oCommandDict[key](self._oStorageManager.db)
        else:
            result = 'database: ' + self._oStorageManager.dbName + ' is not existed'
        self.oAgentCache[key] = result
        self.oSendCache.put(key)
    
    def closeBusyMode(self):
        lastMsg = list()
#        self.oSendCache.put('All is closed')
        self.oInstrRecvCache.put('close')
        self.oInstrSendCache.put('close')
        err1 = self.prcSend.join()
        print('prcSend join return',err1)
        
        #now there is no need to handle msg after 'close'
#        while (True): 
#        #self.oRecvCache.empty() is not reliable, because it can be empty before new item comes, instead, we need to use timeout
#            try: 
#                recvMsg = self.oRecvCache.get(timeout = 3)
#                lastMsg.append(recvMsg)
#            except:
#                break
            
        err2 = self.prcRecv.join() #it seems that if join() wait for too long, it will block forever; Maybe because the deadlock between existed recv process and the queue cache the recv process keeps
        print('prcRecv join return',err2)
        try:
            self.prcRecv.close()
        except:
            self.prcRecv.terminate()
        try:
            self.prcSend.close()
        except:
            self.prcSend.terminate()
        
        if self.logFlag: 
            self.oLog.safeRecord('last Msg: ',False)
            for msg in lastMsg:
                self.oLog.safeRecord(msg, False)
            self.oLog.safeRecord('')
        
        ans = self.oInstrRecvCache.get()
        print(ans)
        if self.logFlag: 
            self.oLog.safeRecordTime(ans)
            
        ans = self.oInstrSendCache.get()
        print(ans)
        if self.logFlag: 
            self.oLog.safeRecordTime(ans)
        
        print('quit busy mode')
        if self.logFlag: 
            self.oLog.safeRecordTime('quit busy mode')
            
        return "CKnowledgeServer_busyMode_close"
    
    def handleCloseMsg(self):
        self.oServer.send('All is closed')
        print('send_msg: All is closed')
        self.oServer.close()
        if self.logFlag: 
            self.oLog.safeRecordTime('send_msg: All is closed')
            self.oLog.safeRecordTime('server_close')
            self.oLog.safeRecord('----------------------------------------')
        self.oCrsProcManager.shutdown()
#                    print("CKnowledgeServer_close")
        return "CKnowledgeServer_close"
    
    def _close(self):
        try:
            try:
                self.prcRecv.close()
            except:
                self.prcRecv.terminate()
            try:
                self.prcSend.close()
            except:
                self.prcSend.terminate()
        except:
            pass
        self.oServer.close()
        self.oCrsProcManager.shutdown()
        if self.logFlag: 
            self.oLog.safeRecordTime('server_close')
            self.oLog.safeRecord('----------------------------------------')
#    def startServer(self):
#        self.oServer.start()
#        while True:
#            tempP = mp.Process(target = listenReq, args=[self.oServer,self.oCache])
#            tempP.start()
#            tempP.join()
#            tempP.close()
#            
#            msg = self.oCache.get()
#            
#            tempP = mp.Process(target = sendRes, args=[self.oServer,msg])
#            tempP.start()
#            tempP.join()
#            tempP.close()
#    
#            if msg == 'close':
#                self.oServer.close()
#                break
from multiprocessing.connection import Client
class CKnowledgeClient:
    
    def __init__(self,addressTuple,authkey=b'secret password',oLog = None):
        self.addressTuple = addressTuple
        self.authkey = authkey
        self.conn = None
        self.oLog = oLog
        
    def connect(self):
        try:
            self.conn = Client(self.addressTuple, authkey=self.authkey)
        except:
            self.modMsg('connection is refused')
            return False
        else:
            return True
    
    def send(self,msg):
        if(not self.closedFlag):
            return self.conn.send(msg)
        else:
            self.modMsg('connection is closed')
            return False
        
    def recv(self,block = False):
        if(not self.closedFlag):
            if block:
                return self.conn.recv()
            if(self.conn.poll()):
                return self.conn.recv()
            else:
                return ReturnCode.WARNING_NOTHING_TO_RECV
        else:
            self.modMsg('connection is closed')
            return False
    
    def close(self):
        if(not self.closedFlag):
            self.conn.send('close')
            try:
                msg = self.conn.recv()
                print(msg)
                if(msg == 'All is closed'):
                    self.conn.close()
                    return True
                else:
                    return False
            except:
                self.modMsg('nothing to recv')
                return False
        else:
            self.modMsg('connection is closed')
            return False
            
    def modMsg(self,string:str):
        string = self.__class__.__name__ + ' warning: ' + string
        if(self.oLog != None):
            self.oLog.safeRecordTime(string)
        else:
            print(string)
    
    @property        
    def closedFlag(self):
        if(self.conn == None ):
            return True
        else:
            return self.conn.closed
    
if __name__ == "__main__":
    args = parse_args()
    print("call with arguments:")
    oKnowledge = CKnowledge(args.name,args.dbPath,args.logFlag)
    try:
        err = oKnowledge.startServer()
    except:
        oKnowledge._close()
    else:
        print(err)
        pass
#    oServer = CServer(('localhost', 6083))
#    oServer.start()
    