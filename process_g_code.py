# ##################################################################
# This script will tweak and modify the G-Code output
# of a slicer.  While it will work on any G-Code file, 
# it's aimed at KISSlicer output because of the comments
# it adds.

# Written by Lars Norpchen
# http://www.octopusmotor.com
# lars@octopusmotor.com
#
# Creative Commons : Share-Alike Non-Commericial Attribution License
#
#
# April 30, 2013  (initial release) 
# May 3, 2013 -- Version 0.8.2 
# * Added min and max temperatures for extruder to keep adjusted temperatures in valid ranges
# * Changed fan option from 'Stacked Sparse Infill' to 'Sparse Infill'
# * added option to enclose LCD messages in quotes
# * bug fix on raft cooling
# May 6, 2013 -- version 0.8.3
# * made stacked infill and support interface cooling start after layer 5
# May 9, 2013 -- Version 0.8.5
# * Added support for relative movement
# * Added parsing of G92 and G28 commands
# * Added support for resume (experimental) 
# * Added ability to remove or pad comment lines
# * Changed the way -m works internally
# * Changed command line option --quote-comments to --quote-messages
# May 28, 2013 -- version 0.8.6
# * Added support for wait on first / all / none temperature setting commands
# * Added option to report flow (extrusion vs travel)
# June 11, 2013 -- version 0.8.8
# * Added support for slicing based on path type, layer, zheight or nth layer
# * Added support for injecting subfiles at path, layer, zheight or nth layer
#   ** Injected files have the z-coordinates stripped out in all move commands
#   ** Filament position, head position, and feed speed are preseved around injected subfile
#   ** Slicing or Injecting do not work well if the slicer does retraction.  Disable retraction in the slicer
#        and use the filament retraction option in this script after slicing / injection operations
# * Addded filament retraction support 
# * Added option to remove header (everything before layer 1 is started) 
# * Added Z-height offset option
# Sept 27, 2013
# * Added support for resume on layer and ZHeight
# * Started support for merging files
# * Added ability to overwrite input file and not require an output file

# Dec 24, 2013 
# * Added scaling of x,y,z axes

#Jan 6, 2014
# * Added metrics and descript.ion support
# * Added ability to specify more than one quality setting type / value
# * Added path and layer detection for Slic3r generated gcode
# * Fixed surplus blank lines and line endings
# * Added progress percentage report
# ##################################################################




import string
import re
import sys
import argparse
import math
import os
import time
import ntpath
# ##################################################################
#globals
args =0
lcd_comment_string = ""
version_string = "%(prog)s 0.9.5"

# some state information
has_raft = 0

current_layer = 0
override_fan_on_this_layer = 0
override_fan_off_this_layer = 0
ext_temperature = 0
bed_temperature=0
fan_speed =0 

# these are used to detect redundant moves
last_x = 0
last_y = 0
last_e = 0
last_f = 0
last_z = 0
peak_x = 0
peak_y = 0
peak_z = 0
min_x  =99999;
min_y = 999999;
#these can be used to determine head speed as well...
delta_x = 0
delta_y = 0
delta_e = 0
delta_f = 0
delta_z = 0
total_e=0
endquote=''
ETA=0
last_path_name = ''
relative_movement = False
linenumber = 0
output_relative_movement = False
#unused at the moment...
last_layer = ""
#layer_height=0
fo = None
foo = None
foa = None
current_file=0
materialname=''

lines = []   
linenumbers = []   
layer_heights = []
last_es = []
max_layer_height =0 
current_output_line = 0
        
def clamp(n, minn, maxn):
    return max(min(maxn, n), minn)
    
# ##################################################################
# insertline
# writes a line to the output file and echos it to the console
def insertline (line, fo):
    global current_output_line    
    current_output_line=current_output_line+1
    fo.write (line + "\n") 
    print ('Adding line at ' + str(current_output_line) + ': ' + line)
        
def insertFile (filename):
    global fo
    insertline (conditional_comment('; BEGIN INSERT ' + filename , True),fo)
    fi = open(filename)
    lines2 = fi.readlines()
    for line2 in lines2:
        line2 = re.sub ("\r"," ",line)
        temp = re.search("^(G[01]\s.*)(Z[0-9.\-]+)(.*)", line2)
        if temp:
            line2 = temp.group(1) + temp.group(3)
        insertline (line2,fo)
    insertline ('G92 E'+str(last_e),fo)
    if output_relative_movement:
        insertline ('G1 X'+str(last_x) + " Y"+str(last_y) +' Z'+str(last_z) +' F'+str(last_f),fo)
    else:
        insertline ('G1 F'+str(last_f),fo)
    insertline (conditional_comment('; END INSERT ' + filename ,True),fo)
    
        
def switchOutput (original, cause):
    global foo,foa,fo, last_x,last_y,last_z,last_e,last_f
    if original and fo!=foo: 
        print ("Switching to original output file " , args.output , " (", cause, ")")
        insertline (conditional_comment('; ------- CUT: ' + cause,True),fo)
        fo = foo
        insertline ('G92 E'+str(last_e),fo)
        if output_relative_movement:
            insertline ('G1 X'+str(last_x) + " Y"+str(last_y) +' Z'+str(last_z) +' F'+str(last_f),fo)
        else:
            insertline ('G1 F'+str(last_f),fo)
        return
    if not original and fo!=foa:
        print ("Switching to alternate output file " , args.split[0] , " (", cause, ")")
        insertline (conditional_comment('; ------- CUT: ' + cause,True),fo)
        fo = foa
        insertline ('G92 E'+str(last_e),fo)
        if output_relative_movement:
            insertline ('G1 X'+str(last_x) + " Y"+str(last_y) +' Z'+str(last_z) +' F'+str(last_f),fo)
        else:
            insertline ('G1 F'+str(last_f),fo)
    

# ##################################################################
# Process_G1_Movement (string)
# returns the modified string
#
# handle all the move related changes and analysis:
#
# 1. strip out unnecessary coordinates from G1 movement commands 
# (where the XYZE or F) is the same as the previous command
# this saves a little space, but not sure it's worth the risk of causing
# confused G-code results.  SHOULD work...but....
# 2. XY use a threshold, so that micromovements can be stripped out
# or quantized (why would you do that?)
# 3. Add offset to XY movements
# 4. Change flow and feed rates

def process_G1_movement (line, command_override):
    global delta_x, delta_y, delta_e, delta_z, delta_f,peak_x,peak_y,peak_z, min_x, min_y
    global last_x, last_y, last_e, last_z, last_f, total_e
    global args ,endquote,relative_movement,output_relative_movement
    comment_remover = re.search("^(.*);(.*$)",line)
    comment = ""
    if comment_remover: 
        if len (comment_remover.groups()) > 1:
            comment = '; ' + comment_remover.group(2)
        line = comment_remover.group(1)
 #   if comment!= "":
 #       print ("Processing line: " , line, " comment is " + comment)
    use_x = args.strip==False    
    Xcoordindates = re.search("X([\+\-0-9\.]*)",line) 
    if Xcoordindates:
        X = float(Xcoordindates.group(1)) * args.scalex 
        if relative_movement:
            X = last_x + X
        delta_x = X - last_x 
        if abs(delta_x) > args.decimate:
            use_x = 1
            last_x = last_x + delta_x
    else:
       use_x = 0
       if args.explicit:
           use_x = 1
           
    Qfactor  = re.search("Q([\+\-0-9\.]*)",line)         
    Afactor  = re.search("A([\+\-0-9\.]*)",line)         
            
    use_y = args.strip==False 
    Ycoordindates = re.search("Y([\+\-0-9\.]*)",line) ;
    if Ycoordindates:
        Y = float(Ycoordindates.group(1)) * args.scaley 
        if relative_movement:
            Y = last_y + Y
        delta_y = Y - last_y 
        if abs(delta_y) > args.decimate:
            use_y = 1
            last_y = last_y + delta_y
    else:
       use_y = 0
       if args.explicit:
           use_y = 1

            

    use_e = args.strip==False    
    Ecoordindates = re.search("E([\+\-0-9\.]*)",line) 
    if Ecoordindates:
        E = float(Ecoordindates.group(1))
        if relative_movement:
            E = last_e + E
        delta_e = E - last_e 
        total_e = total_e + delta_e
        if E!=last_e:
            use_e = 1
            delta_e = E-last_e
            last_e = last_e  + delta_e
    else:
        use_e = 0
        if args.explicit:
           use_e = 1

        
    use_z = args.strip==False   
    Zcoordindates = re.search("Z([\+\-0-9\.]*)",line)   
    if Zcoordindates:
        Z = float(Zcoordindates.group(1))* args.scalez 
        if relative_movement:
            Z = last_z + Z
        delta_z = Z - last_z
        if Z!=last_z:
            use_z = 1
            delta_z = Z-last_z 
            last_z = last_z + delta_z
            layer_heights[current_file]= Z
            SetNextFile()
    else:
        use_z = 0
        if args.explicit:
           use_z = 1
        
    use_f = 0
    if args.explicit:
        use_f = 1
    Feed = re.search("F([\+\-0-9\.]*)",line) 
    if Feed:
    # always use F is given -- need to investigate if it's proper to strip this out!            
        use_f = 1   
        F = float(Feed.group(1))
        if F!=last_f:
#            use_f = 1
            delta_f = last_f - F
            last_f = F

    # rebuild the G1 command
    if use_x==0 and use_y==0 and use_e==0 and use_z==0 and use_f==0:
        return conditional_comment (comment)
    if command_override: 
        line = command_override
    else:
        line = "G1" 
    if Qfactor: 
        line = line + " Q" + Qfactor.group(1) + " "
    if Afactor: 
        line = line + " Q" + Afactor.group(1) + " "
    if output_relative_movement==False:
        if use_x==1:
            line = line + " X" + "{:g}".format(round((last_x + args.xoffset),6) )
        if use_y==1:
            line = line + " Y" +"{:g}".format(round((last_y + args.yoffset),6)) 
        if use_z==1:
            line = line + " Z" +"{:g}".format(round(last_z+ args.zoffset,6) )
        if use_e==1:
            line = line + " E" +"{:g}".format(round(last_e * args.extrusion_flow,6))
    else:
        if use_x==1:
            line = line + " X" + "{:g}".format(round((delta_x + args.xoffset),6) )
            args.xoffset = 0
        if use_y==1:
            line = line + " Y" +"{:g}".format(round((delta_y + args.yoffset),6)) 
            args.yoffset = 0 
        if use_z==1:
            line = line + " Z" +"{:g}".format(round(delta_z+ args.zoffset,6) )
        if use_e==1:
            line = line + " E" +"{:g}".format(round(delta_e * args.extrusion_flow,6))
        
    if use_f==1:    
        line = line + " F" +"{:g}".format(round(last_f * args.feedrate,6)  )
        
    total_distance = math.sqrt (delta_x * delta_x + delta_y * delta_y)
    if total_distance > 0 and args.report_flow: 
        line = line + conditional_comment('; extrude microns per mm = ' + str(1000*delta_e / total_distance) + ' over ' + str(total_distance) + 'mm')
    if last_x > 0: 
        min_x = min (last_x, min_x)
    if last_y > 0: 
        min_y = min (last_y, min_y)
    peak_x = max (last_x, peak_x)
    peak_y = max (last_y, peak_y)
    peak_z = max (last_z, peak_z)
    if args.resume and args.resume[1] >0:
        line = conditional_comment( "; " + line)
    else:
        if args.retract and total_distance > float(args.retract[2]) and delta_e == 0:
            return_pos = last_e
            return_f = last_f
            rp = round(last_e-float(args.retract[0]),6)
            retract_line = conditional_comment ('; Retract, move distance is ' + "{:g}".format(round(total_distance,2))+"mm",True) + process_G1_movement ('G1 E'+"{:g}".format(rp)+' F'+args.retract[1])
            unretract_line = "\n"+conditional_comment ('; Undo Retract',True) + process_G1_movement('G1 E'+"{:g}".format(return_pos)+' F'+args.retract[1])
            # remove the E coordinate from the non-extruding move
            temp = re.search("^(G[01]\s.*)(E[0-9.\-]+)(.*)", line)
            if temp:
                line = temp.group(1) + temp.group(3)
            line = retract_line +  process_G1_movement('G1  F'+str(return_f)) + line + unretract_line + process_G1_movement('G1  F'+str(return_f))

    line = line + conditional_comment (comment)
    return line #+ "\n"
    
    
# process the coordinate resetting and homing commands
# as they affect our position tracking
def process_G92_G28_movement (line,isG92):   
    global delta_x, delta_y, delta_e, delta_z, delta_f
    global last_x, last_y, last_e, last_z, last_f
    global args ,endquote,relative_movement
    axis_found = False
    Xcoordindates = re.search("X([\+\-0-9\.]*)",line) 
    if Xcoordindates:
        axis_found = True
        if isG92:
            last_x = float(Xcoordindates.group(1))  * args.scalex
        else:
            last_x = 0
    Ycoordindates = re.search("Y([\+\-0-9\.]*)",line) 
    if Ycoordindates:
        axis_found = True
        if isG92:
            last_y = float(Ycoordindates.group(1)) * args.scaley
        else:
            last_y = 0
            
    Zcoordindates = re.search("Z([\+\-0-9\.]*)",line) 
    if Zcoordindates:
        axis_found = True
        if isG92:
            last_z = float(Zcoordindates.group(1)) * args.scalez
        else:
            last_z = 0
            
    Ecoordindates = re.search("E([\+\-0-9\.]*)",line) 
    if Ecoordindates:
        axis_found = True
        if isG92:
            last_e = float(Ecoordindates.group(1))
        else:
            last_e = 0
# a G28 or G92 with no axes will home / reset ALL axes            
    if axis_found==False:
        last_x = last_y= last_e = last_z = 0

        
# adjust a comment based on the comment stripping / padding command    
def conditional_comment (str,start_of_line=False):
    global args    
    if str=="":
        return ""
    if args.comments=='remove':
        return ""
    rv = str
    if args.comments=='pad' and start_of_line:
        rv = "G0 " + str
   # if rv[-1] =='\n':
    return rv
    #return rv + "\n"
    
def get_t_code (t, bed=False):
    global args
    if bed:
        tx = args.wait_bed_temp
    else:
        tx = args.wait_temp
    if tx == "none":
        t = "4"
    if tx == "all":
        t = "9"
    if tx == "first":
        t = "9"
        if bed:
            args.wait_bed_temp = "none"
        else:
            args.wait_temp= "none"
    return t   
    
    
def resume():
    global fo,foa,foo,layer_height,linenumber,last_path_name,endquote,fan_speed,args,bed_temperature,current_layer,override_fan_on_this_layer,override_fan_off_this_layer,has_raft,ext_temperature,lcd_comment_string
    args.resume[0] = ""
    args.resume[1] = -1
    insertline (conditional_comment ("; -------------- reset to resume",True),fo)
    print ("Resuming at line " , linenumber)
    if args.print_layer:
       insertline( lcd_comment_string + " Resuming layer " + str(current_layer)+ endquote,fo)

    #resuming print -- set the XY to home, then to the start pos
    insertline ('G28 X0 Y0 '+conditional_comment ('; home x y '),fo)
    insertline ('G1 X'+str(last_x) + " Y"+str(last_y)+conditional_comment (" ; goto last position") ,fo)
    #  reset the Z and E coordinates to where we left off, without moving them
    insertline ('G92 Z'+str(last_z) + " E"+str(last_e) +conditional_comment (" ; reset Z and E positions"),fo)
    insertline ('G1 F'+str(last_f) + conditional_comment (" ; set feedrate"),fo)
    insertline (conditional_comment ("; ----------------- RESUMING HERE",True),fo)
    
    
# #################################################################
# startlayer (string, outputfile) 
# returns the modified line string
#
# do things that happen only when a certain layer starts
# 
# 1. Print the layer number on the LCD
# 2. Do the magic raft cooling trick
# 3. Cool the bed at a certain layer

def startlayer (line): 
    global max_layer_height,fo,foa,foo,layer_height,linenumber,last_path_name,endquote,fan_speed,args,bed_temperature,current_layer,override_fan_on_this_layer,override_fan_off_this_layer,has_raft,ext_temperature,lcd_comment_string,current_output_line
    
    current_layer = current_layer + 1
    print ("---------------------\nProcessing layer # " , current_layer , " starting on line " ,linenumber, " with ZHeight=" , layer_height)
    override_fan_on_this_layer = 0
    override_fan_off_this_layer = 0
    args.no_header = False
    if max_layer_height < layer_height: 
        max_layer_height = layer_height
        
    if args.resume and args.resume[0]=="layer" and args.resume[1] == current_layer:
        resume()
      
    if args.resume and args.resume[0]=="zheight" and layer_height >= args.resume[1]:
        resume()
     
    if args.split:
        if args.split[1]=='layer' :
            if current_layer >= int(args.split[2]):
                switchOutput (False,"Layer >="+ args.split[2])
        elif args.split[1]=='nth' :
            if current_layer > 1 and  (int(current_layer / int(args.split[2]))) % 1 == 0:
                switchOutput (False,"Nth ="+ args.split[2])
            else:
                switchOutput (True,"Nth ="+ args.split[2])
        elif args.split[1]=='zheight' :
            if layer_height >= float(args.split[2]):
                switchOutput (True,"Z height >="+ args.split[2])
    
    
    if args.inject:
        if args.inject[1]=='layer' :
            if current_layer == int(args.inject[2]):
                insertFile (args.inject[0])
        elif args.inject[1]=='nth' :
            if current_layer > 1 and (int(current_layer / int(args.inject[2]))) % 1 == 0:
                insertFile (args.inject[0])
        elif args.inject[1]=='zheight' :
            if layer_height >= float(args.inject[2]):
                insertFile (args.inject[0])
    
# add a layer header and LCD message
    if args.print_layer:
        conditional_comment("; --------------------------------------",True);
        current_output_line=current_output_line+1
        fo.write( lcd_comment_string + "Layer=" + str(current_layer)+ endquote+"\n")
        conditional_comment("; --------------------------------------",True);
        
#start of a new layer number:
    if has_raft==1 and args.cool_raft:
        if current_layer==2 or current_layer==3:
            print ("Adding commands for easier raft removal")
            fan_speed = args.cool_raft[0]
            insertline("M106 S"+str(args.cool_raft[0])+conditional_comment(" ; fan on for raft layer removal",False),fo)
            override_fan_on_this_layer = 1
            override_fan_off_this_layer = 1
        if current_layer==3:
            droptemp = clamp ((args.temperature*ext_temperature)-args.cool_raft[1] ,  args.minimum_temperature,  args.maximum_temperature)
            insertline("M10"+get_t_code("4")+" S"+str(droptemp)+conditional_comment(" ; lowering temp for first object layer",False),fo)
        if current_layer==4:
            insertline("M10"+get_t_code("4")+ " S"+str(args.temperature*ext_temperature)+conditional_comment(" ; setting temp back to normal",False),fo)
            insertline("M107 "+conditional_comment("; fan off completely for second object layer!",False),fo)
            fan_speed =0
            print ("Done processing commands for easier raft removal")
            override_fan_on_this_layer = 1
        
        
    if args.cool_bed and current_layer==args.cool_bed[1]:
        insertline("M1"+get_t_code("4",True)+"0 S"+str(int((args.bed*bed_temperature)-args.cool_bed[0]))+conditional_comment(" ; dropping bed temperature by "+str(args.cool_bed[1]),False),fo)
    return line    

# find which input  file has the lowest height 
def SetNextFile():
    global current_file, lines,fo, last_es,last_e
    if not lines: 
        current_file = -1
        return
    lowestz = 999999
    lowz_index = -1
    for fx in range (0,len(lines)):
        if lowestz > layer_heights[fx]:
            lowestz = layer_heights[fx]
            lowz_index = fx
    if lowz_index!=current_file:
        print ("Switching input files")
        insertline (";  --------  Input switch to file " + str(lowz_index),fo) 
        last_es[current_file] = last_e
        last_e = last_es [lowz_index]
        insertline ('G92 E'+str(last_e) +conditional_comment (" ; recover stored e position"),fo)
    current_file = lowz_index
 
# ##################################################################
def QualitySetting(s):
    try:
        sp = s.split(',')
        qual  = sp[0]
        typ  = sp[1]
        val  = sp[2]
        print ('Quality setting parsed as ' , qual, ' when ', typ ,' is ' ,val)
        return qual,typ,val
    except:
        raise argparse.ArgumentTypeError("Quality needs set of quality level, trigger type and trigger value (ie: path loop")
        
        
def main(argv):
   global layer_height,max_layer_height,linenumbers,current_file,lines,layer_heights,foo,foa,fo,output_relative_movement,relative_movement,linenumber,last_path_name,endquote,version_string,lcd_comment_string,bed_temperature,args,move_threshold,fan_speed,current_layer,override_fan_on_this_layer,override_fan_off_this_layer,has_raft,ext_temperature
   global peak_x, peak_y,peak_z,total_e,min_x,min_y, ETA,materialname,current_output_line
   start_time = time.time()
   
   #deal with the command line: 
   parser = argparse.ArgumentParser(description='Monkey around with GCode (especially from KISSlicer, Slic3r support not thoroughly tested)\nwritten by Lars Norpchen, http://www.octopusmotor.com')
   group1 = parser.add_argument_group( 'File input and output options')
   group1.add_argument('-i', '--input',required = True, metavar='filename',help='specify the input file to process')
   group1.add_argument('-o', '--output',required = False, metavar='filename',help='specify the output file to generate.  If not specified, output will overwrite the input file when done.')
   group1.add_argument('--split', metavar=('filename', '(layer, zheight, nth, or path)','value'),nargs=3,   help='Split the file into a second file based on layer, height or path type.')
   group1.add_argument('--inject', metavar=('filename', '(layer, zheight, nth, or path)','value'),nargs=3,  help='Insert the file snippet based on layer, height or path type.   MUST use relative E coordindates and disable destringing in slicer')
   group1.add_argument('--merge',  metavar=('filename','additional files'), nargs='+', help='Merge the specified file(s). They will be interleaved by layer, sorted based on Z height.  MUST use relative E coordindates and disable destringing in slicer app (you can add retraction commands using the --retract option)') 
   group1.add_argument('--resume', metavar=('line, layer, or Zheight','value'),nargs=2,help='Resume an interrupted print from a given line, layer or ZHeight. X and Y position will be set for you, but you need to manually position the printer\'s Z height before resuming.  Line number is based on the input file, which may change position in the output file based on other post processing commands. ')
 
   group2 = parser.add_argument_group( 'Fan and Temperature control options')
   group2.add_argument('-f', '--fan', metavar='multiplier', type=float, default=1.0, help='Multiply all fan speeds by this.  This only affects fan speeds that were in the original file, not those fan speed commands added by options in this script')
   group2.add_argument('-t', '--temperature', metavar='multiplier', type=float, default=1.0, help='Multiply all extruder temperatures by this. ')
   group2.add_argument('-j', '--minimum-temperature', default = 170, metavar='degrees', type=int,  help='Enforce a minimum temperature for all extruder temperature settings (including raft cooling).  Will not override extruder off (temp=0) commands.')
   group2.add_argument('-n', '--maximum-temperature', default = 250, metavar='degrees', type=int,  help='Enforce a maximum temperature for all extruder temperature settings')
   group2.add_argument('--wait-temp', metavar=('none, first, or all'), choices=('none','all','first'), help='Wait for extruder temperature changes')
   group2.add_argument('--wait-bed-temp', metavar=('none, first, or all'), choices=('none','all','first'), help='Wait for bed temperature changes')
   group2.add_argument('-b', '--bed',  metavar='multiplier',type=float, default=1.0, help='Multiply all bed temps by this')
   group2.add_argument('-k', '--cool-bed',  type=int,nargs=2, metavar=('degrees', 'layer'), help='Slic3r / KISSlicer only. Decrease the bed temperature by DEGREES at specified LAYER')
   group2.add_argument('-q','--cool-support', metavar='fan_speed', type=int, default=0, help='Slic3r / KISSlicer only. Turns the fan on for all "Support Interface" paths. Fan speed is 0 - 255. ')
   group2.add_argument('-g','--cool-sparse-infill', metavar='fan_speed', type=int, default=0, help='Slic3r / KISSlicer only. Turns the fan on for all "Sparse Infill" paths. Fan speed is 0 - 255. ')
   group2.add_argument('-w','--cool-raft',  metavar=('fan_speed', 'temperaturedrop'), nargs=2, type=int, help='Slic3r / KISSlicer only. Adjusts the fan and extrusion temperature to make it easier to remove the raft.  Set the fan speed (0-255) and temperature reduction (in degrees) for first object layer')
 
   group3 = parser.add_argument_group( 'Movement control options')
   group3.add_argument('--quality', nargs="+", action='append', metavar=('quality_setting'), help='Adjust the print quality for a given key (path / layer / etc. -- only path is supported at the moment).  Scales speed, acceleration and jerk values for each extrusion move  -- for example 1.0 is normal, 2.0 is half speed and 0.5 is double speed.  This requires printer firmware support for the Q field in the G0/G1 commands.  Multiple quality options can be set, with each of the three required settings for each option being comma separated (ie: --quality 2,0,path,loop 3.0,path,skirt 0.2,path,perimeter ) . ', dest="quality", type=QualitySetting)
   group3.add_argument('-d', '--decimate',type=float,metavar='mm', default=0, help='Drop XY movements smaller than this.  Useful to get rid of excessive "micromoves" that are below the printer\'s resolution.  Requires "--strip" option enabled to work')
   group3.add_argument('--movement', metavar=('abs or rel') ,choices=('abs','rel'),help='Convert / output all movement to use absolute or relative mode.' )
   group3.add_argument('--scalex',  metavar='x',type=float, default=1.0, help='Scale all X movements by this.  Default is 1.0 (unchanged)')
   group3.add_argument('--scaley',  metavar='x',type=float, default=1.0, help='Scale all Y movements by this. Default is 1.0 (unchanged)')
   group3.add_argument('--scalez',  metavar='x',type=float, default=1.0, help='Scale all Z movements by this. Default is 1.0 (unchanged)')
   group3.add_argument('--scaleall',  metavar='x',type=float, default=1.0, help='Scale all X, Y and Z movements by this. Default is 1.0 (unchanged)')
   group3.add_argument('-x', '--xoffset',  metavar='mm',type=float, default=0, help='Offset all X movements by this.  Use only with absolute coordinate mode.')
   group3.add_argument('-y', '--yoffset', metavar='mm', type=float,  default=0,  help='Offset all Y movements by this.  Use only with absolute coordinate mode.')
   group3.add_argument('-z', '--zoffset', metavar='mm', type=float,  default=0,  help='Offset all Z movements by this.  Use only with absolute coordinate mode.')
   group3.add_argument('-r', '--feedrate', metavar='multiplier', type=float, default=1.0, help='Multiply all movement rates by this (X, Y, Z and Extruder)')
   group3.add_argument('-e', '--extrusion-flow' , metavar='multiplier', type=float,  default=1.0,  help='Multiply extrusion amount by this.')
   group3.add_argument('--retract', metavar=('distance', 'speed','threshold'),nargs=3, help='Retract the filament a given number of mm for non-extrusion moves greater than the threshold (in mm).   Retraction speed is in F code feedrate (mm/min)')
   group4 = parser.add_argument_group( 'Printer user interface options')
   group4.add_argument('-p', '--print-layer', action='store_true', help='Slic3r / KISSlicer only. Print the current layer number on the LCD display')
   group4.add_argument('-v', '--verbose', action='store_true', help='Slic3r / KISSlicer only. Show movement type comments on the LCD display.   This command can be risky on some machines because it adds a lot of extra chatter to the user interface and may cause problems during printing.')
   group4.add_argument('-l','--LCD-command', default='M70', help='Set the G-Code M command for showing a message on the device display.  M117 for Marlin, M70 for ReplicatorG (default)')
   group4.add_argument('--progress', metavar =('GCode_header','lines'),nargs=2, help='Output progress percentage (based on input file lines) every N lines with the given GCode prefix / header (ie: M73 Q).  Will not give proper values if you merge or insert or split files in the same pass. ')
   group4.add_argument('-c', '--colored-movements', action='store_true', help='Slic3r / KISSlicer only. Set RGB LED to show the path type using the M420 command (Makerbot).  This command can be risky on some machines because it adds a lot of extra chatter to the user interface and may cause problems during printing.')
   group4.add_argument('--quote-messages', action='store_true', help='LCD display commands will wrap quotes around the message')
   
   group5 = parser.add_argument_group( 'GCode comments options')
   group5.add_argument('--comments', metavar=('pad or remove'), choices=('pad','remove'),  help='Pad or remove comments from gcode file.  Pad adds an empty move command to the start of comment only lines.  Most hosts will not send comments to printer, however this can cause a line number mismatch between the original file and the printed file (which makes it harder to resume).')
   group5.add_argument('--no-header', action='store_true', help='Remove the header (all commands before the first layer command)')
   group5.add_argument('-m', '--move-header', action='store_true', help='KISSlicer only. Moves the slicing summary at the end of the file to the head of the file')
   group5.add_argument('--description', action='store_true', help='Add metrics data to the system DESCRIPT.ION file for the output file')
   group5.add_argument('--metrics', action='store_true', help='Add comments with metrics data to end of the output file')
   group5.add_argument('--report-flow', action='store_true', help='Report extrusion vs travel rate (micrometers of filament per mm of travel)')
 
  # group6 = parser.add_argument_group('group6', 'General options')
   parser.add_argument('-u','--replace', action='append', metavar=('original', 'replacement'), nargs=2, help='Replace a code with another code. Regex coding is supported (^ for beginning of line, etc). Can be used to comment out codes by adding a ";" to  the code.')
   parser.add_argument('--version', action='version', version=version_string)
 
   group6 = group3.add_mutually_exclusive_group()
   group6.add_argument('--explicit',action='store_true', help='Force all coordinates to be listed in all G0/G1 commands')
   group6.add_argument('-s', '--strip', action='store_true', help='Strip redundant move command parameters. Saves a little space, should not change the result, in theory... use at your own risk!')


    #todo:
        # add pause at layer X
        
   try: 
       args = parser.parse_args()
   except:
     #   parser.print_usage()
        exit()
   endquote = ''
   if args.quote_messages:
       endquote = '"'
   replace_existing_file=False
   inputfile=str.strip(args.input) #[0] 
   if args.output:
       outputfile=str.strip(args.output) #[0]
   else:
       outputfile=None
   if not outputfile:
        outputfile=inputfile+".tmp"
        replace_existing_file=True
   lcd_comment_string =  args.LCD_command+" "+endquote
   args.cool_support = clamp (args.cool_support,0,255)
   args.cool_sparse_infill = clamp (args.cool_sparse_infill,0,255)
   if args.cool_raft:
        args.cool_raft[0] = clamp (args.cool_raft[0],0,255)
   altoutputfile=None
   foa = None
   if args.split:
       altoutputfile = args.split[0]

   if args.scaleall==None:
        args.scaleall= 1.0
   if args.scalex==None:
        args.scalex= 1.0
   if args.scaley==None:
        args.scaley= 1.0
   if args.scalez==None:
        args.scalez= 1.0
   args.scalex=args.scalex * args.scaleall
   args.scaley=args.scaley * args.scaleall
   args.scalez=args.scalez * args.scaleall
   
   
   print ('------------------------------------') 
   print ( args.quality)
   
   print (   'Input file is "', inputfile)
   if not replace_existing_file:
       print ('Output file is "', outputfile)
   else:
       print ('temp output file is "', outputfile, ", which will replace input file when done.")
   fi = open(inputfile)
   fo = open(outputfile,"w")
   if altoutputfile:
        foa = open(altoutputfile,"w")
   foo= fo    
   lines.append (fi.readlines())
   linenumbers.append (0)
   layer_heights.append (0)
   last_es.append (0)
   print ('Read ' +str(len(lines[(len (lines))-1])) + ' lines from ' + inputfile)
   print ('------------------------------------' )
   fanspeedchanged=0
   endline = 1
   
   if args.merge:
        for mf in args.merge:
           # conditional_comment("; Start merging: " + mf,True);
            fm = open(str.strip(mf))
            buf = fm.readlines()
            lines.append (buf)
            linenumbers.append (0)
            layer_heights.append (0)
            last_es.append (0)
           # conditional_comment("; End merging: " + mf + " " + len(buf) +" Lines added" ,True);
            print ('Read ' +str(len(lines[(len (lines))-1])) + ' lines from merge file: ' + mf )
            
   #OK, we're done with the parameters, let's do the work!
# start with a little header processing


   baseline_feedrate = args.feedrate  
   baseline_flowrate = args.extrusion_flow 

   if args.resume:
        args.resume[1] = float(args.resume[1])
        print ("Resume mode: " + args.resume[0] + " at " + str(args.resume[1]))
   if args.move_header:
       lines[0] = lines[0][-30:] + lines[0]
   
   
   if args.movement:
       if args.movement=="abs" or args.movement =="absolute":
            insertline (conditional_comment ("; forced movement absolute mode",True),fo)
            insertline ('G90',fo)
            output_relative_movement = False
       if args.movement=="rel" or args.movement =="relative":
            insertline (conditional_comment ("; forced movement relative mode",True),fo)
            insertline ('G91',fo)
            output_relative_movement = True
            
#process the rest of the file
   current_file =0
   while (True): 
    
        if len(lines[current_file]) <= linenumbers[current_file]:
            del lines[current_file]
            SetNextFile()
            if current_file <0: 
                break

        line = lines[current_file][linenumbers[current_file]]

#   for line in lines[:-endline]:
        linenumbers[current_file] =  linenumbers[current_file]+1
        linenumber= linenumbers[current_file]
        
        if args.resume and args.resume[0]=="line" and args.resume[1] == linenumber:
            resume()
           
        if args.replace:
            for a in args.replace:
                line = re.sub (a[0],a[1]+" ",line)

#first, replace any * in comments as they get confused with checksums
# when we start echoing comments to the LCD display
        line = re.sub ("\*","-",line)
        line = re.sub ("\r"," ",line)
           
#read the fan speed since we may need to set it back later after messing with it
        fan_on = re.search ("^M106\s*S(\d*)",line)
        if fan_on:
            fan_speed = int(fan_on.group(1))
            if args.fan!=1.0:
                newspeed = int(fan_speed*args.fan)
                if newspeed > 255: 
                    newspeed = 255
                if newspeed <0:
                    newspeed =0
                insertline ("M106 S"+str(newspeed)+" ; existing fan speed " +fan_speed+", adjusted by x"+str(args.fan)+ " to " + newspeed,fo);
                line = ""
            else:
                print ("fan speed " + str(fan_speed))
            if override_fan_on_this_layer==1:
                insertline ("; disabled fan on: " + line ,fo);
                line = ""
        fan_off = re.search ("^M107.*",line)
        if fan_off:
            fan_speed = 0
            #print ("fan off")
            if override_fan_off_this_layer==1:
                insertline ("; disabled fan off: " + line ,fo);
                line=""
            
#read the extr temperature                
        temp = re.search("^M10([49]) S(\d*)", line)
        if temp:
            x = int(temp.group(2))
            if x>0: 
                ext_temperature = clamp(int(x*args.temperature), args.minimum_temperature, args.maximum_temperature)
                print ("Extruder temperature command:  " + str(x) + " adjusting to " + str(ext_temperature))
                insertline ("M10"+get_t_code(temp.group(1))+" S"+str(ext_temperature)+" ; existing extruder temp command adjusted",fo)
                line = ""
 #read the bed temperature  -- we'll need that to know what to set it to when we cool it down later in start layers
        temp = re.search("^M1([49])0 S(\d*)", line)
        if temp:
            x = int(temp.group(2))
            bed_temperature = clamp(int (x * args.bed),0,120 )
            print ("Bed temperature command:  " + str(x) + " adjusting to " + str(bed_temperature))
            insertline ("M1"+get_t_code(temp.group(1),True)+"0 S"+str(bed_temperature)+" ; existing bed temp command, adjusted",fo)
            line = ""
            
#check if it's a G0 or G1 movement
        temp = re.search("^G[01]\s+", line)
        if temp:
            line = process_G1_movement (line,"G1")

#G2/G3 ARC movement CW, CCW            
        temp = re.search("^G2\s+", line)
        if temp:
            line = process_G1_movement (line,"G2")

        temp = re.search("^G3\s+", line)
        if temp:
            line = process_G1_movement (line,"G3")
            
            
        temp = re.search("^G90\s+", line)
        if temp:
            relative_movement = False
            if args.movement:
                line = "; " + line
            else:
                output_relative_movement = relative_movement
            
        temp = re.search("^G91\s+", line)
        if temp:
            relative_movement = True
            if args.movement:
                line = "; " + line
            else:
                output_relative_movement = relative_movement

        temp = re.search("^G92\s", line)
        if temp:
            process_G92_G28_movement(line,True)
            if args.resume and args.resume[1] >0:
                line = "; " + line

        temp = re.search("^G28\s", line)
        if temp:
            process_G92_G28_movement(line,False)
            if args.resume and args.resume[1] >0:
                line = "; " + line

  
        # process comment lines:
        #now look for interesting comments, like the path type:        
        comment_tag = re.search(".*;\s+'+(.*)'+(.*)",line)
        if not comment_tag or not comment_tag.group(1):
            # try Slic3r format, which is a comment after every G move
            comment_tag = re.search("G\d\s.*;\s+(.+)",line)
            
        if comment_tag and comment_tag.group(1):
            if last_path_name!=str.lower(comment_tag.group(1)):
                # start a new path
                last_path_name = str.lower(comment_tag.group(1))
                print ('Path type changed to ' + last_path_name)
                if args.verbose:
                    insertline ((lcd_comment_string + last_path_name + +endquote),fo)
            
                       
                # CRAP.  Destring retraction is breaking isolating paths....
                if args.split and args.split[1]=='path': 
                    if str.lower(args.split[2]) in last_path_name:
                        switchOutput (False,"path is "+ args.split[2])
                    else:
                        switchOutput (True,"path is not "+ args.split[2])
                        
                        
                if args.inject and args.inject[1]=='path' : 
                    if str.lower(args.inject[2]) in last_path_name:
                        insertFile (args.inject[0])
                    
    #handle adding the fan commands to start / stop around specific path types      
                if current_layer > 5 and last_path_name=="support unterface" and args.cool_support>0:
                    insertline("M106 S"+str(args.cool_support)+" ; fan on for support interface",fo)
                    args.feedrate = baseline_feedrate * 0.25
                 #   args.extrusion_flow = baseline_flowrate * 2
                 # can't change the flow on the fly without messing up absolute positioning of the E filament!
                 # would work in relative mode tho...
                    fanspeedchanged = 1
                elif current_layer > 5 and last_path_name=="sparse infill" and args.cool_sparse_infill>0:
                    insertline("M106 S"+str(args.cool_sparse_infill)+" ; fan on for sparse infill",fo)
                    args.feedrate = baseline_feedrate * 0.25
                 #   args.extrusion_flow = baseline_flowrate * 2
                    fanspeedchanged = 1
                else:
                    if fanspeedchanged==1:
                        insertline("M106 S"+str(int(fan_speed*args.fan))+" ; set fan speed back to last value",fo)
                        args.feedrate = baseline_feedrate
                        args.extrusion_flow = baseline_flowrate
                        fanspeedchanged=0
                #fo.write (line)
                #continue;
                
        
        # these are color codes for path type, unfortunately settingsetting the I2C BlinkM LED causes 
        # I2C lock ups if done too frequently during an active print job!!
        # however, these commands may work better on another machine that supports like M420 command, 
        # like the makerbot
                if args.colored_movements:
                    loline = str.lower(line)
                    if (re.search("; *'?perimeter'?",loline)):
                        insertline ("M420 R0 E255 B255 "+conditional_comment (" ; set LED to cyan"),fo)
        #            line=re.sub("; 'Perimeter'","M420 R0 E255 B255 \n; 'Perimeter'" ,line)
                    if (re.search("; 'wipe (and de-string)'",loline)):
                        insertline ("M420 R255 E128 B0 "+conditional_comment (" ; set LED to Orange"),fo)
                    if (re.search("; *'?solid'?",loline) or re.search("; *fill",loline)):
                        insertline ("M420 R0 E0 B128 "+conditional_comment (" ; set LED to Dark Blue"),fo)
                    if (re.search("; *'?loop'?",loline)):
                        insertline ("M420 R0 E64 B255  "+conditional_comment (" ; set LED to Lt Blue"),fo)
                    if (re.search("; *'?skirt'?",loline)):
                        insertline ("M420 R220 E255 B64  "+conditional_comment (" ; set LED to Yellow"),fo)

                    if (re.search("; *'?crown'?",loline)):
                        insertline ("M420 R255 E0 B255  "+conditional_comment (" ; set LED to Pink"),fo)
                    if (re.search("; *'?stacked sparse infill'?",loline)):
                        insertline ("M420 R0 E128 B0  "+conditional_comment (" ; set LED to Dk Green"),fo)
                    if (re.search("; *'?sparse infill'?",loline)):
                        insertline ("M420  R0 E255 B0  "+conditional_comment (" ; set LED to Green"),fo)
                    
                    if (re.search("; *'?pillar'?",loline)):
                        insertline ("M420 R255 E0 B0 "+conditional_comment (" ; set LED to Red"),fo)

                    if (re.search("; *'?raft'?",loline)):
                        insertline ("M420 R255 E0 B0 "+conditional_comment (" ; set LED to Red"),fo)
                    if (re.search("; *'?support interface'?",loline)):
                        insertline ("M420 R128 E128 B128 "+conditional_comment (" ; set LED to gray"),fo)

                    if (re.search("; *'?support base'?",loline)):
                        insertline ("M420 R255 E255 B255 "+conditional_comment (" ; set LED to white"),fo)

                    if (re.search("; *'?prime pillar'?",loline)):
                        insertline ("M420 R128 E0 B255"+conditional_comment (" ; set LED to purple"),fo)
            
        if args.quality:
            for q in args.quality:
            #  print ('checking ' , q, ' -> ' ,q[0])
                if q[0][1]=='path':
                    if str.lower(q[0][2]) in last_path_name and delta_e >0:
                #      print ('match path ',last_path_name,' quality set to ',q[0][0])
                        line = re.sub ("G1","G1 Q"+q[0][0]+" ",line)

#check for the raft -- if it does and we have the cool-raft option enabled, we'll deal with it in the start layers function
        if has_raft==0:
#            match = re.search("^;\s+'(Raft)|(Pillar)", line)
            match = re.search(";\s+BEGIN_LAYER_RAFT", line)
            if match: 
                has_raft = 1
                print ("File has raft!")
                #fo.write (line)
                #continue;
            
 #check for the start / end of layer marker, etc,
 
 
        match = re.search(";\s+END_LAYER\S*\sz=([\-0-9.]*)", line)
        if match:
            v = match.group(1)
            layer_heights[current_file] = float(str(v))
            SetNextFile()
        slic3r = False    
        match = re.search(";\s+BEGIN_LAYER\S*\sz=([\-0-9.]*)", line)
        if not match:
            # try to find slic3r's layer comment...
            match = re.search(";\s+move to next layer\S*\s\(([\-0-9.]*)\)", line)
            slic3r = True
        if match:
            v = match.group(1)
            layer_height = float(str(v))
            if slic3r:
                layer_height = last_z
                SetNextFile()
            line = startlayer (line)
            
        if args.print_layer:
            match = re.search(";\s+Estimated Build Time:\s*(.*)", line)
            if match:
                insertline(lcd_comment_string+"ETA "+match.group(1)+endquote,fo)
                ETA = match.group(1)
        match = re.search(";\s+material_name =\s*(.*)", line)
        if match:
            materialname = match.group(1)
    
           
        if args.comments=='remove':
            line=re.sub(";.*$","" ,line)
        if args.comments=='pad':
            comment = re.search("^;(.*)",line)
            if comment: 
                line='G0 ;' + comment.group(1) # + "\n"
        if args.progress and current_output_line>0:
            if current_output_line % int(args.progress[1]) == 0:
                percent = 100 * linenumber / len(lines[current_file])
                insertline (args.progress[0] + str(round(percent,2)),fo)
        line = line.rstrip()    
        if len(line) > 1 and line!='\n' and line !='\r' and not args.no_header:
            fo.write (line+'\n')
            current_output_line=current_output_line+1

   net_layer_height = round(peak_z/current_layer,3)            
   print ('------------------------------------')
   if args.description:
       dec_file_path  = ntpath.dirname (outputfile)
       if dec_file_path:
           dec_file_path=dec_file_path+'\\'+'descript.ion'            
           dec_file = open (dec_file_path,"a")
           if dec_file: 
               dec_file.write ('"' + ntpath.basename (outputfile) +  '"'+ \
                                            ' Src: <' + ntpath.basename (inputfile) + '>' + \
                                            ' Time=' + str(ETA) +\
                                            ' Needs ' + str(round (total_e/1000,2))+'m of ' + materialname +' filament, '+\
                                            str(current_layer) + ' layers of ' + str(net_layer_height) + 'mm, ' + \
                                            'Size:' + str(round(peak_x-min_x,2)) + 'mm x ' + str(round(peak_y-min_y,2)) + 'mm x ' + str(peak_z) + 'mm high,  ' + \
                                            '\n')
               dec_file.close()
   if args.metrics:               
       print (' done processing, adding metrics data')
       insertline (conditional_comment ("; ---------------------------------",True),fo)
       insertline (conditional_comment ("; Post processing report....",True),fo)
       insertline (conditional_comment ("; raw command line options: " + str(sys.argv),True),fo)
       insertline (conditional_comment ("; parsed command line options: " + str(args),True),fo)
       insertline (conditional_comment ("; original filename = " + inputfile,True),fo)
       insertline (conditional_comment ("; time of processing = " + time.strftime("%c"),True),fo)
       elapsed_time = time.time() - start_time
       insertline (conditional_comment ("; time to process = " + str(round(elapsed_time,2)) + ' seconds',True),fo)
       
       
       insertline (conditional_comment ("; total filament = " + str(round (total_e/1000,2)) + "m  ( " + str(round (3.28084*total_e/1000,2)) + " feet)",True),fo)
       insertline (conditional_comment ("; size x = " + format(round(peak_x-min_x,2)) + "mm",True),fo)
       insertline (conditional_comment ("; size y = " + format(round(peak_y-min_y,2)) + "mm",True),fo)
       insertline (conditional_comment ("; limits x = " + str (min_x) + ' to ' + str(peak_x) + "mm",True),fo)
       insertline (conditional_comment ("; limits y = " + str (min_y) + ' to ' + str(peak_y) + "mm",True),fo)
       
       insertline (conditional_comment ("; height  = " + str(peak_z) + "mm",True),fo)
       insertline (conditional_comment ("; layer count  = " + str(current_layer) ,True),fo)
       insertline (conditional_comment ("; net layer height  = " + str(net_layer_height) + "mm",True),fo)
   foo.close()
   if foa:
        foa.close()
   fi.close()
   print ('------------------------------------')

   if replace_existing_file:
        print ("Replacing ",inputfile," with ", outputfile) 
        os.remove (inputfile)
        os.rename (outputfile, inputfile)
    #    os.remove (outputfile)
   print ("Done. ", linenumber, " lines processed")
   if current_layer == 0: 
       print ("Warning -- No layer markers found!")
   else:
       print (current_layer," layers")
   print (max_layer_height," maximum z height")
   
   if args.resume and args.resume[1] > 0:
       print ("Warning -- resume did not find suitable location to restart...output file has no movement commands!")
    


if __name__ == "__main__":
   main(sys.argv[1:])    