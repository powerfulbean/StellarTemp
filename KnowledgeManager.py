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
    
class CCrsProcManager(BaseManager):
    pass

CCrsProcManager.register('server', CServer)
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
                print(recvMsg)
                if(oLog != None):
                    oLog.safeRecordTime('onCallRecv_recv_msg: '+recvMsg)
                err = oRecvCache.put(recvMsg)
#                print(err)
                otherEndCloseFlag = False
        
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
                    print('return')
                    return
                else:
                    pass
        
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
        
    return 0

class CKnowledge:
    
    def __init__(self,name, dbPath:str,logFlag = False):
        self.name = name + '_knowledge'
        self._storageManager:CStorage = CStorageMongoDB(name,dbPath)
        self.address = ('localhost', 6085)
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
        print(self.logFlag)
      
    def configLogger(self):
        dir_list = ['Log']
        oDir = CDirectoryConfig(dir_list,'filesDirectory.conf')
        oDir.checkFolders()
        self.oLog = self.oCrsProcManager.oLog(oDir['Log'],'CKnowledge'+str(datetime.datetime.now().date()))
            
    def startServer(self):
        self.oCrsProcManager.start()
        if(self.logFlag == True):
            self.configLogger()
        self.oServer = self.oCrsProcManager.server(self.address)
        self.oServer.start()
        print('server start')
        if self.logFlag: 
            self.oLog.safeRecordTime('server_start')
        self.prcRecv = mp.Process(target = onCallRecvDaemon, 
                                  args=[self.oServer, self.oInstrRecvCache,self.oRecvCache,self.oLog])
        
        self.prcSend = mp.Process(target = onCallSendDaemon,
                                  args=[self.oServer, self.oInstrSendCache,self.oSendCache,self.oLog])
        self.prcRecv.start()
        self.prcSend.start()
        lastMsg = list()
        while True:
            recvMsg = ''
            try:
                recvMsg = self.oRecvCache.get(block=False)
            except:
                recvMsg = ''
            else:
                if(recvMsg == 'close'):
                    self.oSendCache.put('close'+'newMultiprocess')
                    self.oInstrRecvCache.put('close')
                    self.oInstrSendCache.put('close')
                    err1 = self.prcSend.join()
                    print('prcSend',err1)
                    
                    while (True): #self.oRecvCache.empty() is not reliable, because it can be empty before new item comes
                        try: 
                            recvMsg = self.oRecvCache.get(timeout = 3)
                            lastMsg.append(recvMsg)
                        except:
                            break
                        
                    err2 = self.prcRecv.join() #it seems that if join() wait for too long, it will block forever
                    print('prcRecv',err2)
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
                        
                    self.oServer.close()
                    if self.logFlag: 
                        self.oLog.safeRecordTime('server_close')
                        self.oLog.safeRecord('----------------------------------------')
                    self.oCrsProcManager.shutdown()
#                    print("CKnowledgeServer_close")
                    return "CKnowledgeServer_close"
        return 0
    
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
    