add_library(discord_udp_shim SHARED discord_udp_shim.c)
target_compile_features(discord_udp_shim PRIVATE c_std_11)
target_compile_definitions(discord_udp_shim PRIVATE _GNU_SOURCE)
target_compile_options(discord_udp_shim PRIVATE -Wall -Wextra -Wpedantic -fvisibility=hidden)
target_link_libraries(discord_udp_shim PRIVATE dl pthread)
set_target_properties(discord_udp_shim PROPERTIES
  OUTPUT_NAME discord_udp_shim
  PREFIX "lib"
)
install(TARGETS discord_udp_shim LIBRARY DESTINATION .)

