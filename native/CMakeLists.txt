cmake_minimum_required(VERSION 3.20)
project(discord_local_proxy_native LANGUAGES C CXX)

if(WIN32)
  add_subdirectory(windows)
elseif(UNIX AND NOT APPLE)
  add_subdirectory(linux)
endif()

