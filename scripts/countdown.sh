#!/bin/bash

echo $1
secs=$(($2*1))
while [ $secs -gt 0 ]; do
   echo -ne " Time remaining: $secs\033[0K\r"
   sleep 1
   : $((secs--))
done
echo ' '
echo $3