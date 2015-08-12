#!/usr/bin/python
"""
This program scans the scaler result file (.sca) and printouts some of the
statistics on the screen.
"""

from numpy import *
from glob import glob
from optparse import OptionParser
from pprint import pprint
from functools import partial
from xml.dom import minidom
import math
import os
import random
import re
import sys
import warnings

__all__ = ['parse', 'copyExclude']

class AttrDict(dict):
    """A mapping with string keys that aliases x.y syntax to x['y'] syntax.
    The attribute syntax is easier to read and type than the item syntax.
    """
    def __getattr__(self, name):
        if name not in self:
            self[name] = AttrDict()
        return self[name]
    def __setattr__(self, name, value):
        self[name] = value
    def __delattr__(self, name):
        del self[name]
    def assign(self, path, value):
        """
        Given a hierarchical path such as 'x.y.z' and
        a value, perform an assignment as if the statement
        self.x.y.z had been invoked.
        """
        names = path.split('.')
        container = self
        for name in names[0:-1]:
            if name not in container:
                container[name] = AttrDict()
            container = container[name]
        container[names[-1]] = value
    def access(self, path):
        """
        Given a hierarchical path such as 'x.y.z' returns the value as if the
        statement self.x.y.z had been invoked.
        """
        names = path.split('.')
        container = self
        for name in names[0:-1]:
            if name not in container:
                raise Exception, 'path does not exist: {0}'.format(path)
                container[name] = AttrDict()
            container = container[name]
        return container[names[-1]]

def parse(f):
    """
    Scan a result file containing scalar statistics for omnetsimulation, and
    returns a list of AttrDicts, one containing the metrics for each server.
    """
    hosts = AttrDict() 
    tors = AttrDict()
    aggrs = AttrDict()
    cores = AttrDict()
    currDict = AttrDict()
    for line in f:
        match = re.match('attr\s+network\s+(\S+)',line)
        if match:
            net = match.group(1)
            break
    if not match:
        raise Exception, 'no network name in file: {0}'.format(f.name)
    for line in f:
        match = re.match('(\S+)\s+{0}\.(([a-zA-Z]+).+\.\S+)\s+(".+"|\S+)\s*(\S*)'.format(net), line)
        if match:
            topLevelModule = match.group(3)
            if topLevelModule == 'tor':
                currDict = tors
            elif topLevelModule == 'host':
                currDict = hosts
            elif topLevelModule == 'aggRouter':
                currDict = aggrs
            elif topLevelModule == 'core':
                currDict = cores
            else:
                raise Exception, 'no such module defined for parser: {0}'.format(topLevelModule)
            entryType = match.group(1)
            if entryType == 'statistic':
                var = match.group(2)+'.'+match.group(4)
                currDict.assign(var+'.bins', [])
            elif entryType == 'scalar':
                var = match.group(2)+'.'+match.group(4)
                subVar = var + '.value' 
                value = float(match.group(5))
                currDict.assign(subVar, value)
            else:
                raise Exception, '{0}: not defined for this parser'.format(match.group(1))
            continue
        match = re.match('(\S+)\s+(".+"|\S+)\s+(".+"|\S+)', line)        
        if not match and not line.isspace():
            warnings.warn('Parser cant find a match for line: {0}'.format(line), RuntimeWarning) 
        if currDict:
            entryType = match.group(1)
            subVar = var + '.' + match.group(2)
            value = match.group(3) 
            if entryType == 'field':
                currDict.assign(subVar, float(value))
            elif entryType == 'attr':
                currDict.assign(subVar, value)
            elif entryType == 'bin':
                subVar = var + '.bins'
                valuePair = (float(match.group(2)), float(match.group(3)))
                currDict.access(subVar).append(valuePair)
            else:
                warnings.warn('Entry type not known to parser: {0}'.format(entryType), RuntimeWarning) 
    return hosts, tors, aggrs, cores

def copyExclude(source, dest, exclude):
    selectKeys = (key for key in source if key not in exclude)
    for key in selectKeys:
        if (isinstance(source[key], AttrDict)):
            dest[key] = AttrDict()
            copyExclude(source[key], dest[key], exclude)
        else:
            dest[key] = source[key]

def getStatsFromHist(bins, cumProb, idx):
    if idx == 0 and bins[idx] == -inf:
        return bins[idx + 1]
    return bins[idx]

def getInterestingModuleStats(moduleDic, statsKey, histogramKey):
    moduleStats = AttrDict()
    moduleStats = moduleStats.fromkeys(['count','min','mean','stddev','max','median','threeQuartile','ninety9Percentile'], 0.0)
    histogram = moduleDic.access(histogramKey)
    stats = moduleDic.access(statsKey)
    bins = [tuple[0] for tuple in histogram]
    if stats.count != 0:
        cumProb = cumsum([tuple[1]/stats.count for tuple in histogram])
        moduleStats.count = stats.count
        moduleStats.min = stats.min
        moduleStats.mean = stats.mean
        moduleStats.stddev = stats.stddev
        moduleStats.max = stats.max
        medianIdx = next(idx for idx,value in enumerate(cumProb) if value >= 0.5)
        moduleStats.median = getStatsFromHist(bins, cumProb, medianIdx)
        threeQuartileIdx = next(idx for idx,value in enumerate(cumProb) if value >= 0.75)
        moduleStats.threeQuartile = getStatsFromHist(bins, cumProb, threeQuartileIdx)
        ninety9PercentileIdx = next(idx for idx,value in enumerate(cumProb) if value >= 0.99)
        moduleStats.ninety9Percentile = getStatsFromHist(bins, cumProb, ninety9PercentileIdx)
    return moduleStats

def digestModulesStats(modulesStatsList):
    statsDigest = AttrDict()
    statsDigest = statsDigest.fromkeys(modulesStatsList[0].keys(), 0.0) 
    for targetStat in modulesStatsList:
        statsDigest.count += targetStat.count 
        statsDigest.min += targetStat.min / len(modulesStatsList) 
        statsDigest.max = max(targetStat.max, statsDigest.max)
        statsDigest.mean += targetStat.mean / len(modulesStatsList) 
        statsDigest.stddev += targetStat.stddev / len(modulesStatsList) 
        statsDigest.median += targetStat.median / len(modulesStatsList) 
        statsDigest.threeQuartile += targetStat.threeQuartile / len(modulesStatsList) 
        statsDigest.ninety9Percentile += targetStat.ninety9Percentile / len(modulesStatsList) 
    return statsDigest


def hostQueueWaitTimes(hosts, xmlParsedDic, reportDigest):
    senderIds = xmlParsedDic.senderIds
    # find the queueWaitTimes for different types of packets. Current types
    # considered are request, grant and data packets. Also queueingTimes in the
    # senders NIC.
    keyStrings = ['queueingTime','dataQueueingTime','grantQueueingTime','requestQueueingTime']
    for keyString in keyStrings:
        queuingTimeStats = list()
        for host in hosts.keys():
            hostId = int(re.match('host\[([0-9]+)]', host).group(1))
            queuingTimeHistogramKey = 'host[{0}].eth[0].queue.dataQueue.{1}:histogram.bins'.format(hostId, keyString)
            queuingTimeStatsKey = 'host[{0}].eth[0].queue.dataQueue.{1}:stats'.format(hostId,keyString)
            hostStats = AttrDict()
            if keyString != 'queueingTime' or (keyString == 'queueingTime' and hostId in senderIds):
                hostStats = getInterestingModuleStats(hosts, queuingTimeStatsKey, queuingTimeHistogramKey)
                queuingTimeStats.append(hostStats)

        queuingTimeDigest = AttrDict()
        queuingTimeDigest = digestModulesStats(queuingTimeStats)
        if queuingTimeDigest.count != 0:
            reportDigest.assign('queueWaitTime.hosts.{0}'.format(keyString), queuingTimeDigest)

def torsQueueWaitTime(tors, xmlParsedDic, reportDigest):
    senderHostIds = xmlParsedDic.senderIds
    senderTorIds = [elem for elem in set([int(id / xmlParsedDic.numServersPerTor) for id in senderHostIds])]
    numTorUplinkNics = int(floor(xmlParsedDic.numServersPerTor * xmlParsedDic.nicLinkSpeed / xmlParsedDic.fabricLinkSpeed))
    numServersPerTor = xmlParsedDic.numServersPerTor
    receiverHostIds = xmlParsedDic.receiverIds
    receiverTorIdsIfaces = [(int(id / xmlParsedDic.numServersPerTor), id % xmlParsedDic.numServersPerTor) for id in receiverHostIds]
    keyStrings = ['queueingTime','dataQueueingTime','grantQueueingTime','requestQueueingTime']
    for keyString in keyStrings:
        torsUpwardQueuingTimeStats = list()
        torsDownwardQueuingTimeStats = list()
        for torKey in tors.keys():
            torId = int(re.match('tor\[([0-9]+)]', torKey).group(1))
            tor = tors[torKey]
            # Find the queue waiting times for the upward NICs of sender tors
            # as well as the queue waiting times for various packet types.
            # For the first one we have to find torIds for all the tors
            # connected to the sender hosts
            for ifaceId in range(numServersPerTor, numServersPerTor + numTorUplinkNics):
                # Find the queuewait time only for the upward tor NICs
                queuingTimeHistogramKey = 'eth[{0}].queue.dataQueue.{1}:histogram.bins'.format(ifaceId, keyString)
                queuingTimeStatsKey = 'eth[{0}].queue.dataQueue.{1}:stats'.format(ifaceId, keyString)
                if keyString != 'queueingTime' or (keyString == 'queueingTime' and torId in senderTorIds):
                    torUpwardStat = AttrDict()
                    torUpwardStat = getInterestingModuleStats(tor, queuingTimeStatsKey, queuingTimeHistogramKey)
                    torsUpwardQueuingTimeStats.append(torUpwardStat)

            # Find the queue waiting times for the downward NICs of receiver tors
            # as well as the queue waiting times for various packet types.
            # For the first one we have to find torIds for all the tors
            # connected to the receiver hosts
            for ifaceId in range(0, numServersPerTor):
                # Find the queuewait time only for the downward tor NICs
                queuingTimeHistogramKey = 'eth[{0}].queue.dataQueue.{1}:histogram.bins'.format(ifaceId, keyString)
                queuingTimeStatsKey = 'eth[{0}].queue.dataQueue.{1}:stats'.format(ifaceId, keyString)
                if keyString != 'queueingTime' or (keyString == 'queueingTime' and (torId, ifaceId) in receiverTorIdsIfaces):
                    torDownwardStat = AttrDict() 
                    torDownwardStat = getInterestingModuleStats(tor, queuingTimeStatsKey, queuingTimeHistogramKey)
                    torsDownwardQueuingTimeStats.append(torDownwardStat)
       
        torsUpwardQueuingTimeDigest = AttrDict()
        torsUpwardQueuingTimeDigest = digestModulesStats(torsUpwardQueuingTimeStats)
        if torsUpwardQueuingTimeDigest.count != 0:
            reportDigest.assign('queueWaitTime.tors.upward.{0}'.format(keyString), torsUpwardQueuingTimeDigest)

        torsDownwardQueuingTimeDigest = AttrDict()
        torsDownwardQueuingTimeDigest = digestModulesStats(torsDownwardQueuingTimeStats)
        if torsDownwardQueuingTimeDigest.count != 0:
            reportDigest.assign('queueWaitTime.tors.downward.{0}'.format(keyString), torsDownwardQueuingTimeDigest)

def aggrsQueueWaitTime(aggrs, xmlParsedDic, reportDigest):
    # Find the queue waiting for aggrs switches NICs
    keyStrings = ['queueingTime','dataQueueingTime','grantQueueingTime','requestQueueingTime']
    for keyString in keyStrings:
        aggrsQueuingTimeStats = list()
        for aggr in aggrs.keys():
            for ifaceId in range(0, xmlParsedDic.numTors):
                queuingTimeHistogramKey = '{0}.eth[{1}].queue.dataQueue.{2}:histogram.bins'.format(aggr, ifaceId,  keyString)
                queuingTimeStatsKey = '{0}.eth[{1}].queue.dataQueue.{2}:stats'.format(aggr, ifaceId, keyString)
                aggrsStats = AttrDict()
                aggrsStats = getInterestingModuleStats(aggrs, queuingTimeStatsKey, queuingTimeHistogramKey)
                aggrsQueuingTimeStats.append(aggrsStats)
        
        aggrsQueuingTimeDigest = AttrDict() 
        aggrsQueuingTimeDigest = digestModulesStats(aggrsQueuingTimeStats)
        if aggrsQueuingTimeDigest.count != 0: 
            reportDigest.assign('queueWaitTime.aggrs.{0}'.format(keyString), aggrsQueuingTimeDigest)

def parseXmlFile(xmlConfigFile):
    xmlConfig = minidom.parse(xmlConfigFile)
    xmlParsedDic = AttrDict()
    numServersPerTor = int(xmlConfig.getElementsByTagName('numServersPerTor')[0].firstChild.data)
    numTors = int(xmlConfig.getElementsByTagName('numTors')[0].firstChild.data)
    fabricLinkSpeed = int(xmlConfig.getElementsByTagName('fabricLinkSpeed')[0].firstChild.data)
    nicLinkSpeed = int(xmlConfig.getElementsByTagName('nicLinkSpeed')[0].firstChild.data)
    numTors = int(xmlConfig.getElementsByTagName('numTors')[0].firstChild.data)
    xmlParsedDic.numServersPerTor = numServersPerTor
    xmlParsedDic.numTors = numTors 
    xmlParsedDic.fabricLinkSpeed = fabricLinkSpeed 
    xmlParsedDic.nicLinkSpeed = nicLinkSpeed 
    senderIds = list()
    receiverIds = list()
    allHostsReceive = False
    for hostConfig in xmlConfig.getElementsByTagName('hostConfig'):
        isSender = hostConfig.getElementsByTagName('isSender')[0]
        if isSender.childNodes[0].data == 'true':
            senderIds.append(int(hostConfig.getAttribute('id'))) 
            if allHostsReceive is False:
                destIdsNode = hostConfig.getElementsByTagName('destIds')[0]
                destIds = list()
                if destIdsNode.firstChild != None:
                    destIds = [int(destId) for destId in destIdsNode.firstChild.data.split()]
                if destIds == []:
                    allHostsReceive = True     
                else:
                    receiverIds += destIds
    xmlParsedDic.senderIds = senderIds
    if allHostsReceive is True: 
        receiverIds = range(0, numTors*numServersPerTor)
    xmlParsedDic.receiverIds = [elem for elem in set(receiverIds)]
    return xmlParsedDic

def printStatsLine(statsDic, rowTitle, tw, fw, unit, printKeys):
    if unit == 'us':
        scaleFac = 1e6 
    elif unit == '':
        scaleFac = 1

    printStr = rowTitle.ljust(tw)
    for key in printKeys:
        if key in statsDic.keys():
            if key == 'count':
                printStr += '{0}'.format(int(statsDic.access('count'))).center(fw)
            elif key == 'meanFrac':
                printStr += '{0:.2f}'.format(statsDic.access(key)).center(fw)
            else:
                printStr += '{0:.2f}'.format(statsDic.access(key) * scaleFac).center(fw)
    print(printStr)


def printQueueTimeStats(queueWaitTimeDigest, unit):

    printKeys = ['mean', 'meanFrac', 'stddev', 'min', 'median', 'threeQuartile', 'ninety9Percentile', 'max', 'count']
    tw = 20
    fw = 9 
    lineMax = 100
    title = 'Queue Wait Time Stats'
    print('\n'*2 + ('-'*len(title)).center(lineMax,' ') + '\n' + ('|' + title + '|').center(lineMax, ' ') +
            '\n' + ('-'*len(title)).center(lineMax,' ')) 

    print('\n' + "Packet Type: Requst".center(lineMax,' ') + '\n' + "="*lineMax)
    print("Queue Location".ljust(tw) + 'mean'.format(unit).center(fw) + 'mean'.center(fw) + 'stddev'.format(unit).center(fw) +
            'min'.format(unit).center(fw) + 'median'.format(unit).center(fw) + '75%ile'.format(unit).center(fw) +
            '99%ile'.format(unit).center(fw) + 'max'.format(unit).center(fw) + 'count'.center(fw))
    print("".ljust(tw) + '({0})'.format(unit).center(fw) + '(%)'.center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + ''.center(fw))

    print("_"*lineMax)
    hostStats = queueWaitTimeDigest.queueWaitTime.hosts.requestQueueingTime
    torsUpStats = queueWaitTimeDigest.queueWaitTime.tors.upward.requestQueueingTime
    torsDownStats = queueWaitTimeDigest.queueWaitTime.tors.downward.requestQueueingTime
    aggrsStats = queueWaitTimeDigest.queueWaitTime.aggrs.requestQueueingTime
    meanSum = hostStats.mean + torsUpStats.mean + torsDownStats.mean + aggrsStats.mean
    meanFracSum = 0.0
    for moduleStats in [hostStats, torsUpStats, torsDownStats, aggrsStats]:
        moduleStats.meanFrac = 0 if meanSum==0 else 100*moduleStats.mean/meanSum
        meanFracSum += moduleStats.meanFrac

    printStatsLine(hostStats, 'Host NICs:', tw, fw, unit, printKeys)
    printStatsLine(torsUpStats, 'TORs upward NICs:', tw, fw, unit, printKeys)
    printStatsLine(aggrsStats, 'Aggr Switch NICs:', tw, fw, unit, printKeys)
    printStatsLine(torsDownStats, 'TORs downward NICs:', tw, fw, unit, printKeys)
    print('_'*2*tw + '\n' + 'Total:'.ljust(tw) + '{0:.2f}'.format(meanSum*1e6).center(fw) + '{0:.2f}'.format(meanFracSum).center(fw))

    print('\n\n' + "Packet Type: Grant".center(lineMax,' ') + '\n' + "="*lineMax)
    print("Queue Location".ljust(tw) + 'mean'.format(unit).center(fw) + 'mean'.center(fw) + 'stddev'.format(unit).center(fw) +
            'min'.format(unit).center(fw) + 'median'.format(unit).center(fw) + '75%ile'.format(unit).center(fw) +
            '99%ile'.format(unit).center(fw) + 'max'.format(unit).center(fw) + 'count'.center(fw))
    print("".ljust(tw) + '({0})'.format(unit).center(fw) + '(%)'.center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + ''.center(fw))
    print("_"*lineMax)
    hostStats = queueWaitTimeDigest.queueWaitTime.hosts.grantQueueingTime
    torsUpStats = queueWaitTimeDigest.queueWaitTime.tors.upward.grantQueueingTime
    torsDownStats = queueWaitTimeDigest.queueWaitTime.tors.downward.grantQueueingTime
    aggrsStats = queueWaitTimeDigest.queueWaitTime.aggrs.grantQueueingTime
    meanSum = hostStats.mean + torsUpStats.mean + torsDownStats.mean + aggrsStats.mean
    meanFracSum = 0.0
    for moduleStats in [hostStats, torsUpStats, torsDownStats, aggrsStats]:
        moduleStats.meanFrac = 0 if meanSum==0 else 100*moduleStats.mean/meanSum
        meanFracSum += moduleStats.meanFrac


    printStatsLine(hostStats, 'Host NICs:', tw, fw, unit, printKeys)
    printStatsLine(torsUpStats, 'TORs upward NICs:', tw, fw, unit, printKeys)
    printStatsLine(aggrsStats, 'Aggr Switch NICs:', tw, fw, unit, printKeys)
    printStatsLine(torsDownStats, 'TORs downward NICs:', tw, fw, unit, printKeys)
    print('_'*2*tw + '\n' + 'Total:'.ljust(tw) + '{0:.2f}'.format(meanSum*1e6).center(fw) + '{0:.2f}'.format(meanFracSum).center(fw))

    print('\n\n' + "Packet Type: Data".center(lineMax,' ') + '\n'  + "="*lineMax)
    print("Queue Location".ljust(tw) + 'mean'.format(unit).center(fw) + 'mean'.center(fw) + 'stddev'.format(unit).center(fw) +
            'min'.format(unit).center(fw) + 'median'.format(unit).center(fw) + '75%ile'.format(unit).center(fw) +
            '99%ile'.format(unit).center(fw) + 'max'.format(unit).center(fw) + 'count'.center(fw))
    print("".ljust(tw) + '({0})'.format(unit).center(fw) + '(%)'.center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + ''.center(fw))
    print("_"*lineMax)
    hostStats = queueWaitTimeDigest.queueWaitTime.hosts.dataQueueingTime
    torsUpStats = queueWaitTimeDigest.queueWaitTime.tors.upward.dataQueueingTime
    torsDownStats = queueWaitTimeDigest.queueWaitTime.tors.downward.dataQueueingTime
    aggrsStats = queueWaitTimeDigest.queueWaitTime.aggrs.dataQueueingTime
    meanSum = hostStats.mean + torsUpStats.mean + torsDownStats.mean + aggrsStats.mean
    meanFracSum = 0.0
    for moduleStats in [hostStats, torsUpStats, torsDownStats, aggrsStats]:
        moduleStats.meanFrac = 0 if meanSum==0 else 100*moduleStats.mean/meanSum
        meanFracSum += moduleStats.meanFrac

    printStatsLine(hostStats, 'Host NICs:', tw, fw, unit, printKeys)
    printStatsLine(torsUpStats, 'TORs upward NICs:', tw, fw, unit, printKeys)
    printStatsLine(aggrsStats, 'Aggr Switch NICs:', tw, fw, unit, printKeys)
    printStatsLine(torsDownStats, 'TORs downward NICs:', tw, fw, unit, printKeys)
    print('_'*2*tw + '\n' + 'Total'.ljust(tw) + '{0:.2f}'.format(meanSum*1e6).center(fw) + '{0:.2f}'.format(meanFracSum).center(fw))

    print('\n\n' + "packet Type: All Pkts".center(lineMax,' ') + '\n' + "="*lineMax)
    print("Queue Location".ljust(tw) + 'mean'.format(unit).center(fw) + 'mean'.center(fw) + 'stddev'.format(unit).center(fw) +
            'min'.format(unit).center(fw) + 'median'.format(unit).center(fw) + '75%ile'.format(unit).center(fw) +
            '99%ile'.format(unit).center(fw) + 'max'.format(unit).center(fw) + 'count'.center(fw))
    print("".ljust(tw) + '({0})'.format(unit).center(fw) + '(%)'.center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + ''.center(fw))
    print("_"*lineMax)
    hostStats = queueWaitTimeDigest.queueWaitTime.hosts.queueingTime
    torsUpStats = queueWaitTimeDigest.queueWaitTime.tors.upward.queueingTime
    torsDownStats = queueWaitTimeDigest.queueWaitTime.tors.downward.queueingTime
    aggrsStats = queueWaitTimeDigest.queueWaitTime.aggrs.queueingTime
    meanSum = hostStats.mean + torsUpStats.mean + torsDownStats.mean + aggrsStats.mean
    meanFracSum = 0.0
    for moduleStats in [hostStats, torsUpStats, torsDownStats, aggrsStats]:
        moduleStats.meanFrac = 0 if meanSum==0 else 100*moduleStats.mean/meanSum
        meanFracSum += moduleStats.meanFrac

    printStatsLine(hostStats, 'SX Host NICs:', tw, fw, unit, printKeys)
    printStatsLine(torsUpStats, 'SX TORs UP NICs:', tw, fw, unit, printKeys)
    printStatsLine(aggrsStats, 'Aggr Switch NICs:', tw, fw, unit, printKeys)
    printStatsLine(torsDownStats, 'RX TORs Down NICs:', tw, fw, unit, printKeys)
    print('_'*2*tw + '\n' + 'Total'.ljust(tw) + '{0:.2f}'.format(meanSum*1e6).center(fw) + '{0:.2f}'.format(meanFracSum).center(fw))


def printE2EStretchAndDelay(e2eStretchAndDelayDigest, unit):
    printKeys = ['mean', 'meanFrac', 'stddev', 'min', 'median', 'threeQuartile', 'ninety9Percentile', 'max', 'count']
    tw = 20
    fw = 10 
    lineMax = 100 
    title = 'End To End Message Delays For Different Ranges of Message Sizes'
    print('\n'*2 + ('-'*len(title)).center(lineMax,' ') + '\n' + ('|' + title + '|').center(lineMax, ' ') +
            '\n' + ('-'*len(title)).center(lineMax,' ')) 

    print("="*lineMax)
    print("Msg Size Range".ljust(tw) + 'mean'.format(unit).center(fw) + 'stddev'.format(unit).center(fw) + 'min'.format(unit).center(fw) +
            'median'.format(unit).center(fw) + '75%ile'.format(unit).center(fw) + '99%ile'.format(unit).center(fw) +
            'max'.format(unit).center(fw) + 'count'.center(fw))
    print("Msg Size Range".ljust(tw) + '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) + '({0})'.format(unit).center(fw) +
            '({0})'.format(unit).center(fw) + 'count'.center(fw))

    print("_"*lineMax)
    
    end2EndDelayDigest = e2eStretchAndDelayDigest.delay
    sizeLowBound = ['0'] + [e2eSizedDelay.sizeUpBound for e2eSizedDelay in end2EndDelayDigest[0:len(end2EndDelayDigest)-1]]
    for i, e2eSizedDelay in enumerate(end2EndDelayDigest):
        printStatsLine(e2eSizedDelay, '({0}, {1}]'.format(sizeLowBound[i], e2eSizedDelay.sizeUpBound), tw, fw, 'us', printKeys)

    title = 'End To End Message Stretch For Different Ranges of Message Sizes'
    print('\n'*2 + ('-'*len(title)).center(lineMax,' ') + '\n' + ('|' + title + '|').center(lineMax, ' ') +
            '\n' + ('-'*len(title)).center(lineMax,' ')) 

    print("="*lineMax)
    print("Msg Size Range".ljust(tw) + 'mean'.center(fw) + 'stddev'.center(fw) + 'min'.center(fw) +
            'median'.center(fw) + '75%ile'.center(fw) + '99%ile'.center(fw) + 'max'.center(fw) + 'count'.center(fw))
    print("_"*lineMax)
    
    end2EndStretchDigest = e2eStretchAndDelayDigest.stretch
    sizeLowBound = ['0'] + [e2eSizedStretch.sizeUpBound for e2eSizedStretch in end2EndStretchDigest[0:len(end2EndStretchDigest)-1]]
    for i, e2eSizedStretch in enumerate(end2EndStretchDigest):
        printStatsLine(e2eSizedStretch, '({0}, {1}]'.format(sizeLowBound[i], e2eSizedStretch.sizeUpBound), tw, fw, '', printKeys)

def e2eStretchAndDelay(hosts, xmlParsedDic, e2eStretchAndDelayDigest):
    # For the hosts that are receivers, find the stretch and endToend stats and
    # return them. 
    receiverHostIds = xmlParsedDic.receiverIds 
    sizes = ['1Pkt', '3Pkts', '6Pkts', '13Pkts', '33Pkts', '133Pkts', '1333Pkts', 'Huge']
    e2eStretchAndDelayDigest.delay = []
    e2eStretchAndDelayDigest.stretch = []
    for size in sizes:
        e2eDelayList = list()
        e2eStretchList = list()
        for id in receiverHostIds:
            e2eDelayHistogramKey = 'host[{0}].trafficGeneratorApp[0].msg{1}E2EDelay:histogram.bins'.format(id, size)
            e2eDelayStatsKey = 'host[{0}].trafficGeneratorApp[0].msg{1}E2EDelay:stats'.format(id, size)
            e2eStretchHistogramKey = 'host[{0}].trafficGeneratorApp[0].msg{1}E2EStretch:histogram.bins'.format(id, size)
            e2eStretchStatsKey = 'host[{0}].trafficGeneratorApp[0].msg{1}E2EStretch:stats'.format(id, size)
            e2eDelayForSize = AttrDict()
            e2eStretchForSize = AttrDict()
            e2eDelayForSize = getInterestingModuleStats(hosts, e2eDelayStatsKey, e2eDelayHistogramKey)
            e2eStretchForSize = getInterestingModuleStats(hosts, e2eStretchStatsKey, e2eStretchHistogramKey)
            e2eDelayList.append(e2eDelayForSize)
            e2eStretchList.append(e2eStretchForSize)

        e2eDelayDigest = AttrDict()
        e2eStretchDigest = AttrDict()
        e2eDelayDigest = digestModulesStats(e2eDelayList)
        e2eStretchDigest = digestModulesStats(e2eStretchList)

        e2eDelayDigest.sizeUpBound = '{0}'.format(size)
        e2eStretchAndDelayDigest.delay.append(e2eDelayDigest)
        e2eStretchDigest.sizeUpBound = '{0}'.format(size)
        e2eStretchAndDelayDigest.stretch.append(e2eStretchDigest)
    return e2eStretchAndDelayDigest

def printGenralInfo(xmlParsedDic):
    tw = 20
    fw = 12
    lineMax = 140
    title = 'General Simulation Information'
    print('\n'*2 + ('-'*len(title)).center(lineMax,' ') + '\n' + ('|' + title + '|').center(lineMax, ' ') +
            '\n' + ('-'*len(title)).center(lineMax,' ')) 
    print('Num Servers Per TOR:'.ljust(tw) + '{0}'.format(xmlParsedDic.numServersPerTor).center(fw) + 'Num Sender Hosts:'.ljust(tw) + '{0}'.format(len(xmlParsedDic.senderIds)).center(fw))
    print('Num TORs:'.ljust(tw) + '{0}'.format(xmlParsedDic.numTors).center(fw) + 'Num Receiver Hosts:'.ljust(tw) + '{0}'.format(len(xmlParsedDic.receiverIds)).center(fw))
    print('Server Link Speed:'.ljust(tw) + '{0}Gb/s'.format(xmlParsedDic.nicLinkSpeed).center(fw))
    print('Fabric Link Speed:'.ljust(tw) + '{0}Gb/s'.format(xmlParsedDic.fabricLinkSpeed).center(fw))

def digestTrafficInfo(trafficBytesAndRateDic, title):
    trafficDigest = trafficBytesAndRateDic.trafficDigest
    trafficDigest.title = title 
    if 'bytes' in trafficBytesAndRateDic.keys():
        trafficDigest.cumBytes = sum(trafficBytesAndRateDic.bytes)/1e6
    if 'rates' in trafficBytesAndRateDic.keys():
        trafficDigest.cumRate = sum(trafficBytesAndRateDic.rates)
        trafficDigest.avgRate = trafficDigest.cumRate/float(len(trafficBytesAndRateDic.rates)) 
        trafficDigest.minRate = min(trafficBytesAndRateDic.rates)
        trafficDigest.maxRate = max(trafficBytesAndRateDic.rates)
    if 'dutyCycles' in trafficBytesAndRateDic.keys():
        trafficDigest.avgDutyCycle = sum(trafficBytesAndRateDic.dutyCycles)/float(len(trafficBytesAndRateDic.dutyCycles)) 
        trafficDigest.minDutyCycle = min(trafficBytesAndRateDic.dutyCycles)
        trafficDigest.maxDutyCycle = max(trafficBytesAndRateDic.dutyCycles)

def printBytesAndRates(parsedStats, xmlParsedDic):
    printKeys = ['avgRate', 'cumRate', 'minRate', 'maxRate', 'cumBytes', 'avgDutyCycle', 'minDutyCycle', 'maxDutyCycle']
    tw = 20
    fw = 10
    lineMax = 100
    title = 'Traffic Characteristic (Rates, Bytes, and DutyCycle)'
    print('\n'*2 + ('-'*len(title)).center(lineMax,' ') + '\n' + ('|' + title + '|').center(lineMax, ' ') +
            '\n' + ('-'*len(title)).center(lineMax,' '))
    trafficDic = AttrDict()
    txAppsBytes = trafficDic.sxHostsTraffic.apps.sx.bytes = []
    txAppsRates = trafficDic.sxHostsTraffic.apps.sx.rates = []
    rxAppsBytes = trafficDic.rxHostsTraffic.apps.rx.bytes = []
    rxAppsRates = trafficDic.rxHostsTraffic.apps.rx.rates = []
    txNicsBytes = trafficDic.sxHostsTraffic.nics.sx.bytes = []
    txNicsRates = trafficDic.sxHostsTraffic.nics.sx.rates = []
    txNicsDutyCycles = trafficDic.sxHostsTraffic.nics.sx.dutyCycles = []
    rxNicsBytes = trafficDic.rxHostsTraffic.nics.rx.bytes = []
    rxNicsRates = trafficDic.rxHostsTraffic.nics.rx.rates = []
    rxNicsDutyCycles = trafficDic.rxHostsTraffic.nics.rx.dutyCycles = []

    nicTxBytes = trafficDic.hostsTraffic.nics.sx.bytes = []
    nicTxRates = trafficDic.hostsTraffic.nics.sx.rates = []
    nicTxDutyCycles = trafficDic.hostsTraffic.nics.sx.dutyCycles = []
    nicRxBytes = trafficDic.hostsTraffic.nics.rx.bytes = []
    nicRxRates = trafficDic.hostsTraffic.nics.rx.rates = []
    nicRxDutyCycles = trafficDic.hostsTraffic.nics.rx.dutyCycles = []


    for host in parsedStats.hosts.keys():
        hostId = int(re.match('host\[([0-9]+)]', host).group(1))
        hostStats = parsedStats.hosts[host]
        nicSendBytes = hostStats.access('eth[0].mac.txPk:sum(packetBytes).value')
        nicSendRate = hostStats.access('eth[0].mac.\"bits/sec sent\".value')/1e9
        nicSendDutyCycle = hostStats.access('eth[0].mac.\"tx channel utilization (%)\".value')
        nicRcvBytes = hostStats.access('eth[0].mac.rxPkOk:sum(packetBytes).value')
        nicRcvRate = hostStats.access('eth[0].mac.\"bits/sec rcvd\".value')/1e9
        nicRcvDutyCycle = hostStats.access('eth[0].mac.\"rx channel utilization (%)\".value')
        nicTxBytes.append(nicSendBytes)
        nicTxRates.append(nicSendRate)
        nicTxDutyCycles.append(nicSendDutyCycle)
        nicRxBytes.append(nicRcvBytes)
        nicRxRates.append(nicRcvRate)
        nicRxDutyCycles.append(nicRcvDutyCycle)

        if hostId in xmlParsedDic.senderIds:
            txAppsBytes.append(hostStats.access('trafficGeneratorApp[0].sentMsg:sum(packetBytes).value'))
            txAppsRates.append(hostStats.access('trafficGeneratorApp[0].sentMsg:last(sumPerDuration(packetBytes)).value')*8.0/1e9)
            txNicsBytes.append(nicSendBytes)
            txNicsRates.append(nicSendRate)
            txNicsDutyCycles.append(nicSendDutyCycle)
        if hostId in xmlParsedDic.receiverIds:
            rxAppsBytes.append(hostStats.access('trafficGeneratorApp[0].rcvdMsg:sum(packetBytes).value'))
            rxAppsRates.append(hostStats.access('trafficGeneratorApp[0].rcvdMsg:last(sumPerDuration(packetBytes)).value')*8.0/1e9)
            rxNicsBytes.append(nicRcvBytes)
            rxNicsRates.append(nicRcvRate)
            rxNicsDutyCycles.append(nicRcvDutyCycle)

    upNicsTxBytes = trafficDic.torsTraffic.upNics.sx.bytes = []
    upNicsTxRates = trafficDic.torsTraffic.upNics.sx.rates = []
    upNicsTxDutyCycle = trafficDic.torsTraffic.upNics.sx.dutyCycles = []
    upNicsRxBytes = trafficDic.torsTraffic.upNics.rx.bytes = []
    upNicsRxRates = trafficDic.torsTraffic.upNics.rx.rates = []
    upNicsRxDutyCycle = trafficDic.torsTraffic.upNics.rx.dutyCycles = []
    downNicsTxBytes =  trafficDic.torsTraffic.downNics.sx.bytes = []
    downNicsTxRates =  trafficDic.torsTraffic.downNics.sx.rates = []
    downNicsTxDutyCycle = trafficDic.torsTraffic.downNics.sx.dutyCycles = []
    downNicsRxBytes = trafficDic.torsTraffic.downNics.rx.bytes = []
    downNicsRxRates = trafficDic.torsTraffic.downNics.rx.rates = []
    downNicsRxDutyCycle = trafficDic.torsTraffic.downNics.rx.dutyCycles = []

    numServersPerTor = xmlParsedDic.numServersPerTor
    numTorUplinkNics = int(floor(xmlParsedDic.numServersPerTor * xmlParsedDic.nicLinkSpeed / xmlParsedDic.fabricLinkSpeed))
    for torKey in parsedStats.tors.keys():
        tor = parsedStats.tors[torKey]
        for ifaceId in range(0, numServersPerTor + numTorUplinkNics):
            nicRecvBytes = tor.access('eth[{0}].mac.rxPkOk:sum(packetBytes).value'.format(ifaceId)) 
            nicRecvRates = tor.access('eth[{0}].mac.\"bits/sec rcvd\".value'.format(ifaceId))/1e9
            nicRecvDutyCycle = tor.access('eth[{0}].mac.\"rx channel utilization (%)\".value'.format(ifaceId)) 
            nicSendBytes = tor.access('eth[{0}].mac.txPk:sum(packetBytes).value'.format(ifaceId))
            nicSendRates = tor.access('eth[{0}].mac.\"bits/sec sent\".value'.format(ifaceId))/1e9
            nicSendDutyCycle = tor.access('eth[{0}].mac.\"tx channel utilization (%)\".value'.format(ifaceId))
            if ifaceId < numServersPerTor:
                downNicsRxBytes.append(nicRecvBytes)
                downNicsRxRates.append(nicRecvRates)
                downNicsRxDutyCycle.append(nicRecvDutyCycle)
                downNicsTxBytes.append(nicSendBytes)
                downNicsTxRates.append(nicSendRates)
                downNicsTxDutyCycle.append(nicSendDutyCycle)
            else :
                upNicsRxBytes.append(nicRecvBytes)
                upNicsRxRates.append(nicRecvRates)
                upNicsRxDutyCycle.append(nicRecvDutyCycle)
                upNicsTxBytes.append(nicSendBytes)
                upNicsTxRates.append(nicSendRates)
                upNicsTxDutyCycle.append(nicSendDutyCycle)


    print("="*lineMax)
    print("Measurement Point".ljust(tw) + 'AvgRate'.center(fw) + 'CumRate'.center(fw) + 'MinRate'.center(fw) + 'MaxRate'.center(fw) +
             'CumBytes'.center(fw) + 'Avg Duty'.center(fw) + 'Min Duty'.center(fw) + 'Max Duty'.center(fw))
    print("".ljust(tw) + '(Gb/s)'.center(fw) + '(Gb/s)'.center(fw) + '(Gb/s)'.center(fw) + '(Gb/s)'.center(fw) +
            '(MB)'.center(fw) + 'Cycle(%)'.center(fw) + 'Cycle(%)'.center(fw) + 'Cycle(%)'.center(fw))
   
    print("_"*lineMax)
    digestTrafficInfo(trafficDic.sxHostsTraffic.apps.sx, 'SX Apps Send:')
    printStatsLine(trafficDic.sxHostsTraffic.apps.sx.trafficDigest, trafficDic.sxHostsTraffic.apps.sx.trafficDigest.title, tw, fw, '', printKeys)
    digestTrafficInfo(trafficDic.sxHostsTraffic.nics.sx, 'SX NICs Send:')
    printStatsLine(trafficDic.sxHostsTraffic.nics.sx.trafficDigest, trafficDic.sxHostsTraffic.nics.sx.trafficDigest.title, tw, fw, '', printKeys)
    digestTrafficInfo(trafficDic.hostsTraffic.nics.sx, 'All NICs Send:')
    printStatsLine(trafficDic.hostsTraffic.nics.sx.trafficDigest, trafficDic.hostsTraffic.nics.sx.trafficDigest.title, tw, fw, '', printKeys)


    digestTrafficInfo(trafficDic.torsTraffic.downNics.rx, 'TORs Down Recv:')
    printStatsLine(trafficDic.torsTraffic.downNics.rx.trafficDigest, trafficDic.torsTraffic.downNics.rx.trafficDigest.title, tw, fw, '', printKeys)   
    digestTrafficInfo(trafficDic.torsTraffic.upNics.sx, 'TORs Up Send:')
    printStatsLine(trafficDic.torsTraffic.upNics.sx.trafficDigest, trafficDic.torsTraffic.upNics.sx.trafficDigest.title, tw, fw, '', printKeys)   
    digestTrafficInfo(trafficDic.torsTraffic.upNics.rx, 'TORs Up Recv:')
    printStatsLine(trafficDic.torsTraffic.upNics.rx.trafficDigest, trafficDic.torsTraffic.upNics.rx.trafficDigest.title, tw, fw, '', printKeys)   
    digestTrafficInfo(trafficDic.torsTraffic.downNics.sx, 'TORs Down Send:')
    printStatsLine(trafficDic.torsTraffic.downNics.sx.trafficDigest, trafficDic.torsTraffic.downNics.sx.trafficDigest.title, tw, fw, '', printKeys)   


    digestTrafficInfo(trafficDic.hostsTraffic.nics.rx, 'ALL NICs Recv:')
    printStatsLine(trafficDic.hostsTraffic.nics.rx.trafficDigest, trafficDic.hostsTraffic.nics.rx.trafficDigest.title, tw, fw, '', printKeys)
    digestTrafficInfo(trafficDic.rxHostsTraffic.nics.rx, 'RX NICs Recv:')
    printStatsLine(trafficDic.rxHostsTraffic.nics.rx.trafficDigest, trafficDic.rxHostsTraffic.nics.rx.trafficDigest.title, tw, fw, '', printKeys)
    digestTrafficInfo(trafficDic.rxHostsTraffic.apps.rx, 'RX Apps Recv:')
    printStatsLine(trafficDic.rxHostsTraffic.apps.rx.trafficDigest, trafficDic.rxHostsTraffic.apps.rx.trafficDigest.title, tw, fw, '', printKeys)

def digestQueueLenInfo(queueLenDic, title):
    queueLenDigest = queueLenDic.queueLenDigest
    queueLenDigest.title = title 
    queueLenDigest.meanCnt = sum(queueLenDic.meanCnt)/float(len(queueLenDic.meanCnt))
    queueLenDigest.minCnt = min(queueLenDic.minCnt)
    queueLenDigest.maxCnt = max(queueLenDic.maxCnt)
    queueLenDigest.stddevCnt = sum(queueLenDic.stddevCnt)/float(len(queueLenDic.stddevCnt))
    queueLenDigest.meanBytes = sum(queueLenDic.meanBytes)/float(len(queueLenDic.meanBytes))
    queueLenDigest.minBytes = min(queueLenDic.minBytes)
    queueLenDigest.maxBytes = max(queueLenDic.maxBytes)
    queueLenDigest.stddevBytes = sum(queueLenDic.stddevBytes)/float(len(queueLenDic.stddevBytes))

def printQueueLength(parsedStats, xmlParsedDic):
    printKeys = ['meanCnt', 'stddevCnt', 'meanBytes', 'stddevBytes', 'minCnt', 'minBytes', 'maxCnt', 'maxBytes']
    tw = 15 
    fw = 9 
    lineMax = 88 
    title = 'Queue Length In Number of Packets and Bytes (Stats Collected At Pkt Arrivals)'
    print('\n'*2 + ('-'*len(title)).center(lineMax,' ') + '\n' + ('|' + title + '|').center(lineMax, ' ') +
            '\n' + ('-'*len(title)).center(lineMax,' '))
    print("="*lineMax)
    print("Queue Location".ljust(tw) + 'Mean'.center(fw) + 'StdDev'.center(fw) + 'Mean'.center(fw) + 'StdDev'.center(fw) +
             'Min'.center(fw) + 'Min'.center(fw) + 'Max'.center(fw) + 'Max'.center(fw))
    print("".ljust(tw) + '(Pkts)'.center(fw) + '(Pkts)'.center(fw) + '(KB)'.center(fw) + '(KB)'.center(fw) +
             '(Pkts)'.center(fw) + '(KB)'.center(fw) + '(Pkts)'.center(fw) + '(KB)'.center(fw))
    print("_"*lineMax)
    
    queueLen = AttrDict()
    for key in printKeys:
        queueLen.sxHosts.transport[key] = []
        queueLen.sxHosts.nic[key] = []
        queueLen.hosts.nic[key] = []
        queueLen.tors.up.nic[key] = []
        queueLen.sxTors.up.nic[key] = []
        queueLen.tors.down.nic[key] = []
        queueLen.rxTors.down.nic[key] = []
    
    for host in parsedStats.hosts.keys():
        hostId = int(re.match('host\[([0-9]+)]', host).group(1))
        hostStats = parsedStats.hosts[host]
        transQueueLenMin = hostStats.access('transportScheme.msgsLeftToSend:stats.min')
        transQueueLenMax = hostStats.access('transportScheme.msgsLeftToSend:stats.max')
        transQueueLenMean = hostStats.access('transportScheme.msgsLeftToSend:stats.mean')
        transQueueLenStddev = hostStats.access('transportScheme.msgsLeftToSend:stats.stddev')
        transQueueBytesMin = hostStats.access('transportScheme.bytesLeftToSend:stats.min')/1e3
        transQueueBytesMax = hostStats.access('transportScheme.bytesLeftToSend:stats.max')/1e3
        transQueueBytesMean = hostStats.access('transportScheme.bytesLeftToSend:stats.mean')/1e3
        transQueueBytesStddev = hostStats.access('transportScheme.bytesLeftToSend:stats.stddev')/1e3
        
        nicQueueLenMin = hostStats.access('eth[0].queue.dataQueue.queueLength:stats.min')
        nicQueueLenMax = hostStats.access('eth[0].queue.dataQueue.queueLength:stats.max')
        nicQueueLenMean = hostStats.access('eth[0].queue.dataQueue.queueLength:stats.mean')
        nicQueueLenStddev = hostStats.access('eth[0].queue.dataQueue.queueLength:stats.stddev')
        nicQueueBytesMin = hostStats.access('eth[0].queue.dataQueue.queueByteLength:stats.min')/1e3
        nicQueueBytesMax = hostStats.access('eth[0].queue.dataQueue.queueByteLength:stats.max')/1e3
        nicQueueBytesMean = hostStats.access('eth[0].queue.dataQueue.queueByteLength:stats.mean')/1e3
        nicQueueBytesStddev = hostStats.access('eth[0].queue.dataQueue.queueByteLength:stats.stddev')/1e3
        queueLen.hosts.nic.minCnt.append(nicQueueLenMin)
        queueLen.hosts.nic.maxCnt.append(nicQueueLenMax)
        queueLen.hosts.nic.meanCnt.append(nicQueueLenMean)
        queueLen.hosts.nic.stddevCnt.append(nicQueueLenStddev)
        queueLen.hosts.nic.minBytes.append(nicQueueBytesMin)
        queueLen.hosts.nic.maxBytes.append(nicQueueBytesMax)
        queueLen.hosts.nic.meanBytes.append(nicQueueBytesMean)
        queueLen.hosts.nic.stddevBytes.append(nicQueueBytesStddev)

        if hostId in xmlParsedDic.senderIds:
            queueLen.sxHosts.transport.minCnt.append(transQueueLenMin)
            queueLen.sxHosts.transport.maxCnt.append(transQueueLenMax)
            queueLen.sxHosts.transport.meanCnt.append(transQueueLenMean)
            queueLen.sxHosts.transport.stddevCnt.append(transQueueLenStddev)
            queueLen.sxHosts.transport.minBytes.append(transQueueBytesMin)
            queueLen.sxHosts.transport.maxBytes.append(transQueueBytesMax)
            queueLen.sxHosts.transport.meanBytes.append(transQueueBytesMean)
            queueLen.sxHosts.transport.stddevBytes.append(transQueueBytesStddev)
            
            queueLen.sxHosts.nic.minCnt.append(nicQueueLenMin)
            queueLen.sxHosts.nic.maxCnt.append(nicQueueLenMax)
            queueLen.sxHosts.nic.meanCnt.append(nicQueueLenMean)
            queueLen.sxHosts.nic.stddevCnt.append(nicQueueLenStddev)
            queueLen.sxHosts.nic.minBytes.append(nicQueueBytesMin)
            queueLen.sxHosts.nic.maxBytes.append(nicQueueBytesMax)
            queueLen.sxHosts.nic.meanBytes.append(nicQueueBytesMean)
            queueLen.sxHosts.nic.stddevBytes.append(nicQueueBytesStddev)

    numServersPerTor = xmlParsedDic.numServersPerTor
    numTorUplinkNics = int(floor(xmlParsedDic.numServersPerTor * xmlParsedDic.nicLinkSpeed / xmlParsedDic.fabricLinkSpeed))
    senderHostIds = xmlParsedDic.senderIds
    senderTorIds = [elem for elem in set([int(id / xmlParsedDic.numServersPerTor) for id in senderHostIds])]
    receiverHostIds = xmlParsedDic.receiverIds
    receiverTorIdsIfaces = [(int(id / xmlParsedDic.numServersPerTor), id % xmlParsedDic.numServersPerTor) for id in receiverHostIds]

    for torKey in parsedStats.tors.keys():
        tor = parsedStats.tors[torKey]
        torId = int(re.match('tor\[([0-9]+)]', torKey).group(1))
        for ifaceId in range(0, numServersPerTor + numTorUplinkNics):
            nicQueueLenMin = tor.access('eth[{0}].queue.dataQueue.queueLength:stats.min'.format(ifaceId))
            nicQueueLenMax = tor.access('eth[{0}].queue.dataQueue.queueLength:stats.max'.format(ifaceId))
            nicQueueLenMean = tor.access('eth[{0}].queue.dataQueue.queueLength:stats.mean'.format(ifaceId))
            nicQueueLenStddev = tor.access('eth[{0}].queue.dataQueue.queueLength:stats.stddev'.format(ifaceId))
            nicQueueBytesMin = tor.access('eth[{0}].queue.dataQueue.queueByteLength:stats.min'.format(ifaceId))/1e3
            nicQueueBytesMax = tor.access('eth[{0}].queue.dataQueue.queueByteLength:stats.max'.format(ifaceId))/1e3
            nicQueueBytesMean = tor.access('eth[{0}].queue.dataQueue.queueByteLength:stats.mean'.format(ifaceId))/1e3
            nicQueueBytesStddev = tor.access('eth[{0}].queue.dataQueue.queueByteLength:stats.stddev'.format(ifaceId))/1e3

            if ifaceId < numServersPerTor:
                queueLen.tors.down.nic.minCnt.append(nicQueueLenMin)
                queueLen.tors.down.nic.maxCnt.append(nicQueueLenMax)
                queueLen.tors.down.nic.meanCnt.append(nicQueueLenMean)
                queueLen.tors.down.nic.stddevCnt.append(nicQueueLenStddev)
                queueLen.tors.down.nic.minBytes.append(nicQueueBytesMin)
                queueLen.tors.down.nic.maxBytes.append(nicQueueBytesMax)
                queueLen.tors.down.nic.meanBytes.append(nicQueueBytesMean)
                queueLen.tors.down.nic.stddevBytes.append(nicQueueBytesStddev)

                if (torId, ifaceId) in receiverTorIdsIfaces:
                    queueLen.rxTors.down.nic.minCnt.append(nicQueueLenMin)
                    queueLen.rxTors.down.nic.maxCnt.append(nicQueueLenMax)
                    queueLen.rxTors.down.nic.meanCnt.append(nicQueueLenMean)
                    queueLen.rxTors.down.nic.stddevCnt.append(nicQueueLenStddev)
                    queueLen.rxTors.down.nic.minBytes.append(nicQueueBytesMin)
                    queueLen.rxTors.down.nic.maxBytes.append(nicQueueBytesMax)
                    queueLen.rxTors.down.nic.meanBytes.append(nicQueueBytesMean)
                    queueLen.rxTors.down.nic.stddevBytes.append(nicQueueBytesStddev)

            else:
                queueLen.tors.up.nic.minCnt.append(nicQueueLenMin)
                queueLen.tors.up.nic.maxCnt.append(nicQueueLenMax)
                queueLen.tors.up.nic.meanCnt.append(nicQueueLenMean)
                queueLen.tors.up.nic.stddevCnt.append(nicQueueLenStddev)
                queueLen.tors.up.nic.minBytes.append(nicQueueBytesMin)
                queueLen.tors.up.nic.maxBytes.append(nicQueueBytesMax)
                queueLen.tors.up.nic.meanBytes.append(nicQueueBytesMean)
                queueLen.tors.up.nic.stddevBytes.append(nicQueueBytesStddev)

                if torId in senderTorIds:
                    queueLen.sxTors.up.nic.minCnt.append(nicQueueLenMin)
                    queueLen.sxTors.up.nic.maxCnt.append(nicQueueLenMax)
                    queueLen.sxTors.up.nic.meanCnt.append(nicQueueLenMean)
                    queueLen.sxTors.up.nic.stddevCnt.append(nicQueueLenStddev)
                    queueLen.sxTors.up.nic.minBytes.append(nicQueueBytesMin)
                    queueLen.sxTors.up.nic.maxBytes.append(nicQueueBytesMax)
                    queueLen.sxTors.up.nic.meanBytes.append(nicQueueBytesMean)
                    queueLen.sxTors.up.nic.stddevBytes.append(nicQueueBytesStddev)


    digestQueueLenInfo(queueLen.sxHosts.transport, 'SX Transports')
    printStatsLine(queueLen.sxHosts.transport.queueLenDigest, queueLen.sxHosts.transport.queueLenDigest.title, tw, fw, '', printKeys)
    digestQueueLenInfo(queueLen.sxHosts.nic, 'SX NICs')
    printStatsLine(queueLen.sxHosts.nic.queueLenDigest, queueLen.sxHosts.nic.queueLenDigest.title, tw, fw, '', printKeys)
    digestQueueLenInfo(queueLen.hosts.nic, 'All NICs')
    printStatsLine(queueLen.hosts.nic.queueLenDigest, queueLen.hosts.nic.queueLenDigest.title, tw, fw, '', printKeys)
    digestQueueLenInfo(queueLen.sxTors.up.nic, 'SX TORs Up')
    printStatsLine(queueLen.sxTors.up.nic.queueLenDigest, queueLen.sxTors.up.nic.queueLenDigest.title, tw, fw, '', printKeys)
    digestQueueLenInfo(queueLen.tors.up.nic, 'All TORs Up')
    printStatsLine(queueLen.tors.up.nic.queueLenDigest, queueLen.tors.up.nic.queueLenDigest.title, tw, fw, '', printKeys)
    digestQueueLenInfo(queueLen.rxTors.down.nic, 'RX TORs Down')
    printStatsLine(queueLen.rxTors.down.nic.queueLenDigest, queueLen.rxTors.down.nic.queueLenDigest.title, tw, fw, '', printKeys)
    digestQueueLenInfo(queueLen.tors.down.nic, 'All TORs Down')
    printStatsLine(queueLen.tors.down.nic.queueLenDigest, queueLen.tors.down.nic.queueLenDigest.title, tw, fw, '', printKeys)

   
def main():
    parser = OptionParser()
    options, args = parser.parse_args()
    if len(args) > 0:
        scalarResultFile = args[0]
    else:
        scalarResultFile = 'homatransport/src/dcntopo/results/RecordAllStats-0.sca'

    xmlConfigFile = 'homatransport/src/dcntopo/config.xml' 
    xmlParsedDic = AttrDict()
    xmlParsedDic = parseXmlFile(xmlConfigFile)
    parsedStats = AttrDict() 
    parsedStats.hosts, parsedStats.tors, parsedStats.aggrs, parsedStats.cores  = parse(open(scalarResultFile))
    #pprint(parsedStats)
    queueWaitTimeDigest = AttrDict()
    hostQueueWaitTimes(parsedStats.hosts, xmlParsedDic, queueWaitTimeDigest)
    torsQueueWaitTime(parsedStats.tors, xmlParsedDic, queueWaitTimeDigest)
    aggrsQueueWaitTime(parsedStats.aggrs, xmlParsedDic, queueWaitTimeDigest)
    printGenralInfo(xmlParsedDic)
    printBytesAndRates(parsedStats, xmlParsedDic)
    printQueueLength(parsedStats, xmlParsedDic)
    printQueueTimeStats(queueWaitTimeDigest, 'us')
    e2eStretchAndDelayDigest = AttrDict()
    e2eStretchAndDelay(parsedStats.hosts, xmlParsedDic, e2eStretchAndDelayDigest)
    printE2EStretchAndDelay(e2eStretchAndDelayDigest, 'us')

if __name__ == '__main__':
    sys.exit(main());
