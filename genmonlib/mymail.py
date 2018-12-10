#!/usr/bin/env python
#-------------------------------------------------------------------------------
#    FILE: mymail.py
# PURPOSE: send mail messages and receive commands via email
#
#  AUTHOR: Jason G Yates
#    DATE: 18-Nov-2016
#
# MODIFICATIONS:
#-------------------------------------------------------------------------------

import datetime, time, smtplib, threading
import imaplib, email, email.header
import os
from os.path import basename
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import COMMASPACE, formatdate

import atexit
from shutil import copyfile

import mylog, mythread, mysupport, myconfig

#imaplib.Debug = 4


#------------ MyMail class -----------------------------------------------------
class MyMail(mysupport.MySupport):
    def __init__(self, monitor = False, incoming_folder = None, processed_folder = None, incoming_callback = None, localinit = False, loglocation = "/var/log/", ConfigFilePath = None, start = True):

        self.Monitor = monitor                          # true if we receive IMAP email
        self.IncomingFolder = incoming_folder           # folder to look for incoming email
        self.ProcessedFolder = processed_folder         # folder to move mail to once processed
        self.IncomingCallback = incoming_callback       # called back with mail subject as a parameter
        if ConfigFilePath == None:
            self.ConfigFilePath = "/etc/"
        else:
            self.ConfigFilePath = ConfigFilePath
        self.Mailbox = 0
        self.EmailSendQueue = []                        # queue for email to send
        self.DisableEmail = False
        self.DisableIMAP = False
        self.DisableSNMP = False
        self.SSLEnabled = False
        self.UseBCC = False
        self.Threads = {}                               # Dict of mythread objects
        self.ModulePath = os.path.dirname(os.path.dirname(os.path.realpath(__file__))) + "/"
        # log errors in this module to a file
        if localinit == True:
            self.logfile = "mymail.log"
            self.configfile = "mymail.conf"
        else:
            self.logfile = loglocation + "mymail.log"
            self.configfile = self.ConfigFilePath + "mymail.conf"

        self.log = mylog.SetupLogger("mymail", self.logfile)

        # if mymail.conf is not in the /etc directory attempt to copy it from the
        # main source directory
        if not os.path.isfile(self.configfile):
            if os.path.isfile(self.ModulePath + "mymail.conf"):
                copyfile(self.ModulePath + "mymail.conf" , self.configfile)
            else:
                self.LogConsole("Missing config file : " + self.configfile)
                self.LogError("Missing config file : " + self.configfile)
                sys.exit(1)


        self.config = myconfig.MyConfig(filename = self.configfile, section = "MyMail", log = self.log)

        self.GetConfig()

        if self.DisableEmail:
            self.DisableIMAP = True
            self.DisableSNMP = True
            self.Monitor = False

        if not len(self.SMTPServer):
            self.DisableSNMP = True

        if not len(self.IMAPServer):
            self.DisableIMAP = True
            self.Monitor = False

        atexit.register(self.Close)

        if not self.DisableEmail:
            if not self.DisableSMTP and self.SMTPServer != "":
                self.Threads["SendMailThread"] = mythread.MyThread(self.SendMailThread, Name = "SendMailThread", start = start)
            else:
                self.LogError("SMTP disabled")

            if not self.DisableIMAP and self.Monitor and self.IMAPServer != "":     # if True then we will have an IMAP monitor thread
                if incoming_callback and incoming_folder and processed_folder:
                    self.Threads["EmailCommandThread"] = mythread.MyThread(self.EmailCommandThread, Name = "EmailCommandThread", start = start)
                else:
                    self.FatalError("ERROR: incoming_callback, incoming_folder and processed_folder are required if receive IMAP is used")
            else:
                self.LogError("IMAP disabled")

    #---------- MyMail.GetConfig -----------------------------------------------
    def GetConfig(self, reload = False):

        try:

            if self.config.HasOption('disableemail'):
                self.DisableEmail = self.config.ReadValue('disableemail', return_type = bool)
            else:
                self.DisableEmail = False

            if self.config.HasOption('disablesmtp'):
                self.DisableSMTP = self.config.ReadValue('disablesmtp', return_type = bool)
            else:
                self.DisableSMTP = False

            if self.config.HasOption('disableimap'):
                self.DisableIMAP = self.config.ReadValue('disableimap', return_type = bool)
            else:
                self.DisableIMAP = False

            if self.config.HasOption('usebcc'):
                self.UseBCC = self.config.ReadValue('usebcc', return_type = bool)

            self.EmailPassword = self.config.ReadValue('email_pw')
            self.EmailAccount = self.config.ReadValue('email_account')
            if self.config.HasOption('sender_account'):
                self.SenderAccount = self.config.ReadValue('sender_account')
            else:
                self.SenderAccount = self.EmailAccount
            # SMTP Recepients
            self.EmailRecipient = self.config.ReadValue('email_recipient')
            self.EmailRecipientByType = {}
            for type in ["outage", "error", "warn", "info"]:
                tempList = []
                for email in self.EmailRecipient.split(','):
                    if self.config.HasOption(email):
                       if type in self.config.ReadValue(email).split(','):
                          tempList.append(email)
                    else:
                       tempList.append(email)

                self.EmailRecipientByType[type] = ",".join(tempList)
            # SMTP Server
            if self.config.HasOption('smtp_server'):
                self.SMTPServer = self.config.ReadValue('smtp_server')
                self.SMTPServer = self.SMTPServer.strip()
            else:
                self.SMTPServer = ""
            # IMAP Server
            if self.config.HasOption('imap_server'):
                self.IMAPServer = self.config.ReadValue('imap_server')
                self.IMAPServer = self.IMAPServer.strip()
            else:
                self.IMAPServer = ""
            self.SMTPPort = self.config.ReadValue('smtp_port', return_type = int)

            if self.config.HasOption('ssl_enabled'):
                self.SSLEnabled = self.config.ReadValue('ssl_enabled', return_type = bool)
        except Exception as e1:
                self.LogErrorLine("ERROR: Unable to read config file : " + str(e1))
                sys.exit(1)

        return True

    #---------- MyMail.Close ---------------------------------------------------
    def Close(self):
        try:
            if not self.DisableEmail:
                if self.SMTPServer != "" and not self.DisableSMTP:
                    try:
                        self.Threads["SendMailThread"].Stop()
                    except:
                        pass

            if not self.DisableEmail:
                if self.Monitor and self.IMAPServer != "" and not self.DisableIMAP:
                    if self.IncomingCallback != None and self.IncomingFolder != None and self.ProcessedFolder != None:
                        try:
                            self.Threads["EmailCommandThread"].Stop()
                        except:
                            pass

            if self.Monitor:
                if self.Mailbox:
                    try:
                        self.Mailbox.close()
                        self.Mailbox.logout()
                    except:
                        pass
        except Exception as e1:
            self.LogErrorLine("Error Closing Mail: " + str(e1))

    #---------- MyMail.EmailCommandThread --------------------------------------
    def EmailCommandThread(self):

        while True:
            # start email command thread
            try:
                self.Mailbox = imaplib.IMAP4_SSL(self.IMAPServer)
            except Exception:
                self.LogError( "No Internet Connection! ")
                if self.WaitForExit("EmailCommandThread", 120 ):
                    return
                continue   # exit thread
            try:
                data = self.Mailbox.login(self.EmailAccount, self.EmailPassword)
            except Exception:
                self.LogError( "LOGIN FAILED!!! ")
                if self.WaitForExit("EmailCommandThread", 60 ):
                    return
                continue   # exit thread
            while True:
                try:
                    rv, data = self.Mailbox.select(self.IncomingFolder)
                    if rv != 'OK':
                        self.LogError( "Error selecting mail folder! (select)")
                        if self.WaitForExit("EmailCommandThread", 15 ):
                            return
                        continue
                    rv, data = self.Mailbox.search(None, "ALL")
                    if rv != 'OK':
                        self.LogError( "No messages found! (search)")
                        if self.WaitForExit("EmailCommandThread", 15 ):
                            return
                        continue
                    for num in data[0].split():
                        rv, data = self.Mailbox.fetch(num, '(RFC822)')
                        if rv != 'OK':
                            self.LogError( "ERROR getting message (fetch): " + str(num))
                            if self.WaitForExit("EmailCommandThread", 15 ):
                                return
                            continue
                        msg = email.message_from_string(data[0][1])
                        decode = email.header.decode_header(msg['Subject'])[0]
                        subject = unicode(decode[0])

                        self.IncomingCallback(subject)

                        # move the message to processed folder
                        result = self.Mailbox.store(num, '+X-GM-LABELS', self.ProcessedFolder)  #add the label
                        self.Mailbox.store(num, '+FLAGS', '\\Deleted')     # this is needed to remove the original label
                    if self.WaitForExit("EmailCommandThread", 15 ):
                        return
                except Exception as e1:
                    self.LogErrorLine("Resetting email thread" + str(e1))
                    if self.WaitForExit("EmailCommandThread", 60 ):  # 60 sec
                        return
                    break

            if self.WaitForExit("EmailCommandThread", 15 ):  # 15 sec
                return

            ## end of outer loop

    #------------ MyMail.sendEmailDirectMIME -----------------------------------
    # send email, bypass queue
    def sendEmailDirectMIME(self, msgtype, subjectstr, msgstr, recipient = None, files=None, deletefile = False):

        if recipient == None:
            recipient = self.EmailRecipientByType[msgtype]

        # update date
        dtstamp=datetime.datetime.now().strftime('%a %d-%b-%Y')
        # update time
        tmstamp=datetime.datetime.now().strftime('%I:%M:%S %p')

        msg = MIMEMultipart()
        msg['From'] = self.SenderAccount
        if self.UseBCC:
            msg['Bcc'] = recipient
        else:
            msg['To'] = recipient
        msg['Date'] = formatdate(localtime=True)
        msg['Subject'] = subjectstr

        body = '\n' + 'Time: ' + tmstamp + '\n' + 'Date: ' + dtstamp + '\n' + msgstr
        msg.attach(MIMEText(body, 'plain', 'us-ascii'))

        # if the files are not found then we skip them but still send the email
        try:
            for f in files or []:

                with open(f, "rb") as fil:
                    part = MIMEApplication(
                        fil.read(),
                        Name=basename(f)
                    )
                    part['Content-Disposition'] = 'attachment; filename="%s"' % basename(f)
                    msg.attach(part)

                if deletefile:
                    os.remove(f)

        except Exception as e1:
            self.LogErrorLine("Error attaching file in sendEmailDirectMIME: " + str(e1))

        #self.LogError("Logging in: SMTP Server <"+self.SMTPServer+">:Port <"+str(self.SMTPPort) + ">")

        try:
            if self.SSLEnabled:
                 session = smtplib.SMTP_SSL(self.SMTPServer, self.SMTPPort)
                 session.ehlo()
            else:
                 session = smtplib.SMTP(self.SMTPServer, self.SMTPPort)
                 session.starttls()
                 session.ehlo
                 # this allows support for simple TLS
        except Exception as e1:
            self.LogErrorLine("Error SMTP Init : SSL:<" + str(self.SSLEnabled)  + ">: " + str(e1))
            return False

        try:
            if self.EmailPassword != "":
                session.login(str(self.EmailAccount), str(self.EmailPassword))

            if "," in recipient:
                multiple_recipients = recipient.split(",")
                session.sendmail(self.SenderAccount, multiple_recipients, msg.as_string())
            else:
                session.sendmail(self.SenderAccount, recipient, msg.as_string())
        except Exception as e1:
            self.LogErrorLine("Error SMTP sendmail: " + str(e1))
            session.quit()
            return False

        session.quit()

        return True
       # end sendEmailDirectMIME()

    #------------MyMail::SendMailThread-----------------------------------------
    def SendMailThread(self):

        # once sendMail is called email messages are queued and then sent from this thread
        time.sleep(0.1)
        while True:

            while self.EmailSendQueue != []:
                MailError = False
                EmailItems = self.EmailSendQueue.pop()
                try:
                    if not (self.sendEmailDirectMIME(EmailItems[0], EmailItems[1], EmailItems[2], EmailItems[3], EmailItems[4], EmailItems[5])):
                        self.LogError("Error in SendMailThread, sendEmailDirectMIME failed, retrying")
                        MailError = True
                except Exception as e1:
                    # put the time back at the end of the queue
                    self.LogErrorLine("Error in SendMailThread, retrying (2): " + str(e1))
                    MailError = True

                if MailError:
                    self.EmailSendQueue.insert(len(self.EmailSendQueue),EmailItems)
                    # sleep for 2 min and try again
                    if self.WaitForExit("SendMailThread", 120 ):
                        return

            if self.WaitForExit("SendMailThread", 2 ):
                return

    #------------MyMail::sendEmail----------------------------------------------
    # msg type must be one of "outage", "error", "warn", "info"
    def sendEmail(self, subjectstr, msgstr, recipient = None, files = None, deletefile = False, msgtype = "error"):

        if not self.DisableEmail:       # if all email disabled, do not queue
            if self.SMTPServer != "" and not self.DisableSMTP:    # if only sending is disabled, do not queue
                self.EmailSendQueue.insert(0,[msgtype,subjectstr,msgstr,recipient, files, deletefile])
