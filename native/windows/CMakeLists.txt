include(FetchContent)

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
FetchContent_Declare(
  minhook
  GIT_REPOSITORY https://github.com/TsudaKageyu/minhook.git
  GIT_TAG c3fcafdc10146beb5919319d0683e44e3c30d537
  GIT_SHALLOW FALSE
)
FetchContent_MakeAvailable(minhook)

add_library(discord_udp_shim_windows SHARED discord_udp_shim.cpp version.def)
target_compile_features(discord_udp_shim_windows PRIVATE cxx_std_20)
target_compile_definitions(discord_udp_shim_windows PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_compile_options(discord_udp_shim_windows PRIVATE /W4 /permissive- /EHsc)
target_link_libraries(discord_udp_shim_windows PRIVATE minhook ws2_32)
set_target_properties(discord_udp_shim_windows PROPERTIES
  OUTPUT_NAME version
  PREFIX ""
)
install(TARGETS discord_udp_shim_windows RUNTIME DESTINATION .)

