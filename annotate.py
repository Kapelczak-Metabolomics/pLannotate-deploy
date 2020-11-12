import numpy as np
import base64

from Bio.Seq import Seq
from Bio import SeqIO
from Bio.SeqRecord import SeqRecord
import subprocess
from Bio.SeqFeature import SeqFeature, FeatureLocation
from tempfile import NamedTemporaryFile
import pandas as pd
import streamlit as st

def BLAST(seq,wordsize=12, db='nr_db', BLASTtype="n", flags = 'qstart qend sseqid sframe pident slen sseq length sstart send qlen'):
    query = NamedTemporaryFile()
    tmp = NamedTemporaryFile()
    SeqIO.write(SeqRecord(Seq(seq), id="temp"), query.name, "fasta")

#     subprocess.call(
#         (f'blast{BLASTtype} -query {query.name} -out {tmp.name} ' #pi needed?
#         f'-db {db} -max_target_seqs 20000 -culling_limit 10 -word_size {str(wordsize)} -outfmt "6 {flags}"'),
#         shell=True)
    subprocess.call(
        (f'blast{BLASTtype} -task blastn-short -query {query.name} -out {tmp.name} -perc_identity 95 ' #pi needed?
        f'-db {db} -max_target_seqs 20000 -culling_limit 10 -word_size {str(wordsize)} -outfmt "6 {flags}"'),
        shell=True)
    
    with open(tmp.name, "r") as file_handle:  #opens BLAST file
        align = file_handle.readlines()

    tmp.close()
    query.close()

    inDf=pd.DataFrame([ele.split() for ele in align],columns=flags.split())
    inDf=inDf.apply(pd.to_numeric, errors='ignore')

    return inDf

def calc_level(inDf):
    #calculates the level to be rendered at
    #this must happen while the concatentated seq df still exists
    inDf['level']=0
    for i in inDf.index:
        df=inDf[inDf.index<i]
        s=inDf.loc[i]['qstart']
        e=inDf.loc[i]['qend']
        startBound=((df['qstart']<=s) & (df['qend']>=s))
        endBound=((df['qstart']<=e) & (df['qend']>=e))
        #st.write(startBound.sum())
        if df[startBound].empty ^ df[endBound].empty: 
            level=1 # ^ == XOR
        else: 
            level=0
        within=df[startBound&endBound]
        #st.write(within)
        inDf.at[i,'level'] = level
    return inDf

def clean_and_calculate(inDf):

    inDf['qstart'] = inDf['qstart'].astype('int')
    inDf['qstart'] = inDf['qstart']-1
    inDf['qend']   = inDf['qend']-1
    inDf['percmatch'] = (inDf['length']/inDf['slen']*100)
    inDf[['sseqid','type']] = inDf['sseqid'].str.split("|", n=1, expand=True)
    inDf['sseqid'] = inDf['sseqid'].str.replace(".gb","")#gb artifact from BLASTdb
    inDf['abs percmatch'] = 100-abs(100-inDf['percmatch'])#eg changes 102.1->97.9
    inDf['pi_permatch'] = (inDf["pident"]*inDf["abs percmatch"])/100
    inDf['score']    = (inDf['pi_permatch']/100)*inDf["length"]
    inDf['qlen']     = (inDf['qlen']/2).astype('int')
    inDf['fragment'] = inDf["percmatch"] < 95
    inDf['Feature']  = inDf['sseqid'].str.replace("_"," ") #idk where this happened in other scripts
    
    wiggleSize = 0.15 #this is the percent "trimmed" on either end eg 0.1 == 80%
    inDf['wiggle'] = (inDf['length'] * wiggleSize).astype(int)
    inDf['wstart'] =  inDf['qstart'] + inDf['wiggle']
    inDf['wend']   =  inDf['qend']   - inDf['wiggle']
    
    inDf=inDf.sort_values(by=["score","length","percmatch"], ascending=[False, False, False])
    
    #subtracts a full plasLen if longer than tot length
    inDf['qstart'] = np.where(inDf['qstart'] >= inDf['qlen'], inDf['qstart'] - inDf['qlen'], inDf['qstart'])    
    inDf['qend']   = np.where(inDf['qend']   >= inDf['qlen'], inDf['qend']   - inDf['qlen'], inDf['qend'])

    inDf['wstart'] = np.where(inDf['wstart'] >= inDf['qlen'], inDf['wstart'] - inDf['qlen'], inDf['wstart'])    
    inDf['wend']   = np.where(inDf['wend']   >= inDf['qlen'], inDf['wend']   - inDf['qlen'], inDf['wend'])

    inDf=inDf.drop_duplicates()
    inDf=inDf.reset_index(drop=True)

    inDf=calc_level(inDf)

    #create a conceptual sequence space
    seqSpace=[]
    for i in inDf.index:
        end    = inDf['qlen'][0]
        wstart = inDf.loc[i]['wstart'] #changed from qstart
        wend   = inDf.loc[i]['wend']   #changed from qend

        sseqid = [inDf.loc[i]['sseqid']]

        if wend < wstart: # if hit crosses ori
            left   = (wend + 1)          * [1]
            center = (wstart - wend - 1) * [0]
            right  = (end  - wstart + 0) * [1]
        else: # if normal
            left   =  wstart             * [0]
            center = (wend - wstart + 1) * [1]
            right  = (end  - wend   - 1) * [0]

        seqSpace.append(sseqid+left+center+right)
    seqSpace=pd.DataFrame(seqSpace,columns=['sseqid']+list(range(0,end)))
    seqSpace=seqSpace.set_index([seqSpace.index,'sseqid'])

    #filter through overlaps in sequence space
    toDrop=set()
    for i in range(len(seqSpace)):

        if seqSpace.iloc[0].name in toDrop:
            continue #adds no speed up, but I prefer it conceptually

        qstart = inDf.loc[seqSpace.iloc[i].name[0]]['qstart']
        qend   = inDf.loc[seqSpace.iloc[i].name[0]]['qend']
        
        #columnSlice=seqSpace.columns[(seqSpace.iloc[i]==1)] #only columns of hit
        if qstart < qend:
            columnSlice = list(range(qstart, qend+1))
        else:
            columnSlice = list(range(0,qend + 1)) + list(range(qstart, end))

        rowSlice=seqSpace[columnSlice].any(1) #only the rows that are in the columns of hit
        toDrop=toDrop|set(seqSpace[rowSlice].loc[i+1:].index) #add the indexs below the current to the drop-set
    seqSpace=seqSpace.drop(toDrop)
    inDf=inDf.loc[seqSpace.index.get_level_values(0)] #needs shared index labels to work
    inDf=inDf.reset_index(drop=True)
    
    return inDf

def FeatureLocation_smart(r):
    #creates compound locations if needed
    if r.qend>r.qstart:
        return FeatureLocation(r.qstart, r.qend, r.sframe)
    elif r.qstart>r.qend:
        first=FeatureLocation(r.qstart, r.qlen, r.sframe)
        second=FeatureLocation(0, r.qend, r.sframe)
        if r.sframe == 1 or r.sframe == 0:
            return first+second
        elif r.sframe == -1:
            return second+first
def get_gbk(inDf,inSeq):
    #this could be passed a more annotated df
    inDf=inDf.reset_index(drop=True)

    #adds a FeatureLocation object so it can be used in gbk construction
    inDf['feat loc']=inDf.apply(FeatureLocation_smart, axis=1)
    #make a record
    record = SeqRecord(seq=Seq(inSeq),name='pLannotate')
    record.annotations["topology"] = "circular"
    for index in inDf.index:
        record.features.append(SeqFeature(
            inDf.loc[index]['feat loc'],
            type=inDf.loc[index]["type"],
            qualifiers={"label": inDf.loc[index]["sseqid"].replace("_"," "),
            "identity":inDf.loc[index]["pident"],
            "match length":inDf.loc[index]["percmatch"],
            "Other:":inDf.loc[index]["type"]}))

    #converts gbk into straight text
    outfileloc=NamedTemporaryFile()
    with open(outfileloc.name, "w") as handle:
        record.annotations["molecule_type"] = "DNA"
        SeqIO.write(record, handle, "genbank")
    with open(outfileloc.name) as handle:
        record=handle.read()
    outfileloc.close()

    return record
def annotate(inSeq):
    database="./BLAST_dbs/full_snapgene_feature_list_w_types_db"

    #I could just create a seq object? this could catch errors though
    fileloc = NamedTemporaryFile()
    SeqIO.write(SeqRecord(Seq(inSeq),name="pLannotate"), fileloc.name, 'fasta')
    record=list(SeqIO.parse(fileloc.name, "fasta"))
    fileloc.close()

    assert len(record)==1,f"FASTA file contains ~multitudes~ --> please submit a single FASTA file."
    record=record[0]

    query=str(record.seq)*2

    blastDf = BLAST(seq=query,wordsize=12, db=database, BLASTtype="n")
    if blastDf.empty: #if no hits are found
        return blastDf
        
    blastDf = clean_and_calculate(blastDf)

    smallHits=blastDf[blastDf['slen']<25]
    smallHits=smallHits[smallHits["pident"] >= ((smallHits["slen"]-1)/smallHits["slen"])*100] #allows for 1 mismatch
    smallHits=smallHits[smallHits["percmatch"] >= ((smallHits["slen"]-1)/smallHits["slen"])*100]
    smallHits['small']=True

    normHits=blastDf[blastDf['slen']>=25]
    normHits=normHits[normHits['length']>=18]
    normHits=normHits[normHits['pident']>=95]
    normHits['small']=False

    hits=smallHits.append(normHits)
    hits=hits.set_index(['Feature'])

    return hits

