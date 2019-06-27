#!/bin/bash
set -x
export SCRIPT_HOME=$PWD/scripts

# OSX only
[ `uname -s` != "Darwin" ] && echo 'OS X Only' &&return

function iterm () {
    local cmd=""
    local wd="$1"
    local args="$@"

    cmd="echo Launching Vagrant VM"
    for var in "$@"
    do
        cmd="$cmd;$var"
    done

    echo $cmd
   # osascript &>/dev/null <<EOF
    osascript <<EOF
tell application "iTerm"
	activate
	set new_window to (create window with default profile)
	set cSession to current session of new_window
	tell new_window
		tell cSession
			delay 1
			write text "cd $wd;$cmd"
			delay 2
			repeat
				delay 0.1
				--          display dialog cSession is at shell prompt
				set isdone to is at shell prompt
				if isdone then exit repeat
			end repeat
		end tell
	end tell
end tell
EOF
}

iterm $@ $SCRIPT_HOME "./bring_up_upload_svc.sh"  &

$SCRIPT_HOME/countdown.sh 'Waiting for file upload services to startup' 90 'Services are ready!'
iterm $@ $SCRIPT_HOME "./bring_up_consumer.sh" &
iterm $@ $SCRIPT_HOME "./bring_up_yupana.sh" &

$SCRIPT_HOME/countdown.sh 'Waiting for host inventory db to be ready.' 30 'Services are ready!'
iterm $@ $SCRIPT_HOME "./bring_up_host_inventory_svc.sh" &
