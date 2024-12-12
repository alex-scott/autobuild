#!/bin/bash

cp autobuild.service /etc/systemd/system/
systemctl status autobuild
systemctl enable autobuild

#systemctl -l status autobuild

#Если есть ошибки — читаем вывод в статусе, исправляем, не забываем после исправлений в юните перегружать демон systemd
#systemctl daemon-reload