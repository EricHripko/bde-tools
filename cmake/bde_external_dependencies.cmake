if(BDE_EXTERNAL_DEPENDENCIES_INCLUDED)
    return()
endif()
set(BDE_EXTERNAL_DEPENDENCIES_INCLUDED true)

include(bde_log)
include(bde_utils)

# External packages
find_package(PkgConfig)

# :: bde_import_target_raw_library ::
# -----------------------------------------------------------------------------
# The function tries to find the static library by name.
# If the library is found the function creates imported interface target
# that can be used in target_link_libraries().
function(bde_import_target_raw_library libName)
    bde_assert_no_extra_args()

    # The dependency was resolved already.
    if (TARGET ${libName})
        return()
    endif()

    bde_log(VERBOSE "Searching raw library: ${libName}")
    # find_library is limited to search only in the specified
    # distribution refroot directory.

    # TODO: Might add the hints for lookup path.
    set(libraryPath "${DISTRIBUTION_REFROOT}/opt/bb/${bde_install_lib_suffix}")

    find_library(
        rawLib_${libName}
        NAMES
            ${CMAKE_STATIC_LIBRARY_PREFIX}${libName}.${bde_install_ufid}${CMAKE_STATIC_LIBRARY_SUFFIX}
            ${CMAKE_STATIC_LIBRARY_PREFIX}${libName}${CMAKE_STATIC_LIBRARY_SUFFIX}
        HINTS
            "${libraryPath}"
            NO_DEFAULT_PATH
    )
    if(rawLib_${libName})
        bde_log(VERBOSE "Found(raw): ${rawLib_${libName}}")
        add_library(${libName} INTERFACE IMPORTED)
        set_property(
            TARGET ${libName}
            PROPERTY
                INTERFACE_LINK_LIBRARIES "${rawLib_${libName}}"
        )
    endif()
endfunction()

# :: bde_import_target_from_pc ::
# -----------------------------------------------------------------------------
# The function tries to find the static library using the pkg-config file(s)
# If the library is found the function creates an imported target that
# can be used in target_link_libraries(). The imported target has necessary
# transitive dependencies.
# The function returns a list of additional dependencies found in
# the .pc file.
function(bde_import_target_from_pc retDeps depName)
    bde_assert_no_extra_args()

    # The dependency was resolved already.
    if (TARGET ${depName})
        bde_return()
    endif()

    if(NOT ${PKG_CONFIG_FOUND})
        bde_return()
    endif()

    # TODO: Might add the hints for lookup path and .pc file patterns.
    set(libraryPath "${DISTRIBUTION_REFROOT}/opt/bb/${bde_install_lib_suffix}")

    # The SYSROOT_DIR will be added by pkg config to the library and include pathes
    # by pkg-config.
    set(ENV{PKG_CONFIG_SYSROOT_DIR} "${DISTRIBUTION_REFROOT}")
    # This is a location for .pc files.
    set(ENV{PKG_CONFIG_PATH} "${libraryPath}/pkgconfig")

    foreach(pcName "${depName}.${bde_install_ufid}"
                   "${CMAKE_STATIC_LIBRARY_PREFIX}${depName}.${bde_install_ufid}"
                   "${depName}lib.${bde_install_ufid}"
                    "${depName}"
                   "${CMAKE_STATIC_LIBRARY_PREFIX}${depName}"
                   "${depName}lib")
        pkg_check_modules(${depName}_pc QUIET "${pcName}")

        if(${depName}_pc_FOUND)
            break()
        endif()
    endforeach()

    if(NOT ${depName}_pc_FOUND)
        bde_return()
    endif()

    # STATIC_LIBRARIES contains transitive dependencies
    set(staticDeps "${${depName}_pc_STATIC_LIBRARIES}")

    set(searchHints "NO_CMAKE_PATH;NO_CMAKE_ENVIRONMENT_PATH")

    foreach(flag IN LISTS ${depName}_pc_LDFLAGS)
        if(flag MATCHES "^-L(.*)")
            # only look into the given paths from now on
            set(searchHints HINTS ${CMAKE_MATCH_1} NO_DEFAULT_PATH)
            continue()
        endif()
        if(flag MATCHES "^-l(.*)")
            set(pkgName "${CMAKE_MATCH_1}")
            if(TARGET pkgName)
                continue()
            endif()
        else()
            message(WARNING "Unknown flag is found in .pc file ${${depName}_pc_LDFLAGS}")
            continue()
        endif()

        # Searching raw library
        if ((pkgName STREQUAL depName)
            AND NOT TARGET ${depName})
            find_library(
                rawLib_${depName}
                NAMES
                    ${CMAKE_STATIC_LIBRARY_PREFIX}${depName}.${bde_install_ufid}${CMAKE_STATIC_LIBRARY_SUFFIX}
                    ${CMAKE_STATIC_LIBRARY_PREFIX}${depName}${CMAKE_STATIC_LIBRARY_SUFFIX}
                    ${searchHints}
            )

            if(rawLib_${depName})
                list(REMOVE_ITEM staticDeps ${depName})

                bde_log(VERBOSE "External dependency: ${depName}")
                bde_log(VERBOSE "  Using: ${rawLib_${depName}}")
                if (staticDeps)
                    bde_log(VERBOSE "  Depends on: ${staticDeps}")
                endif()

                add_library(${depName} UNKNOWN IMPORTED)

                if(${depName}_pc_INCLUDE_DIRS)
                    set_property(
                        TARGET ${depName}
                        PROPERTY
                            INTERFACE_INCLUDE_DIRECTORIES "${${depName}_pc_INCLUDE_DIRS}"
                    )
                endif()

                set_property(
                    TARGET ${depName}
                    PROPERTY
                    IMPORTED_LOCATION "${rawLib_${depName}}"
                )

                if(staticDeps)
                    set_property(
                        TARGET ${depName}
                        APPEND PROPERTY
                            INTERFACE_LINK_LIBRARIES "${staticDeps}"
                    )
                endif()

                if(${depName}_pc_FLAGS_OTHER)
                    set_property(
                        TARGET ${depName}
                        PROPERTY
                            INTERFACE_COMPILE_OPTIONS "${${depName}_pc_CFLAGS_OTHER}"
                    )
                endif()
            endif()
            break()
        endif()
    endforeach()

    bde_return(${staticDeps})
endfunction()

# :: bde_resolve_external_dependencies ::
# -----------------------------------------------------------------------------
# The function tries to resolve all external dependencies in the following
# order:
# 1. CMake config
# 2. .pc file
# 3. Raw static library.
#
# If the dependency (library) is found, the library is added to the link
# line (the order is maintained using dependency information found in the
# CMake config or .pc files)
# If the dependency (library) is not found, the '-l<depName>' line is added
# to the link line.
function(bde_resolve_external_dependencies externalDeps)
    bde_assert_no_extra_args()

    set(deps ${externalDeps})

    while(deps)
        list(REMOVE_DUPLICATES deps)
        set(currentDeps "${deps}")
        set(deps)

        bde_log(VERBOSE "Active dependencies: ${currentDeps}")

        foreach(depName IN LISTS currentDeps)
            if(NOT depName OR TARGET ${depName})
                continue()
            endif()

            bde_log(VERBOSE "Processing ${depName}")

            # Looking up CMake export for the external dependency.
            find_package(
                ${depName} QUIET
                PATH_SUFFIXES "${bde_install_lib_suffix}/${bde_install_ufid}/cmake"
            )

            if(TARGET ${depName})
                continue()
            endif()

            # CMake EXPORT set is not found. Trying pkg-config.
            set(newDeps)
            bde_import_target_from_pc(newDeps "${depName}")
            list(APPEND deps "${newDeps}")

            if(TARGET ${depName})
                continue()
            endif()

            bde_import_target_raw_library("${depName}")

            if(TARGET ${depName})
                continue()
            endif()

            # The external dependancy is not found. Creating fake target to stop lookup.
            # Unresolved dependencies will be added as-is with '-l' flag to the link line.
            message(STATUS  "Not found (raw) external dependency '${depName}'")
            add_custom_target(${depName})
        endforeach()
    endwhile()
endfunction()
