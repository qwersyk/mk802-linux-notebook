ENTRY(ram_exec_hook)
SECTIONS {
 . = 0x61f00000;
 .marker : { *(.text.entry) }
 ASSERT(SIZEOF(.marker) <= 128, "marker too large")
 . = 0x61f00200;
 .exec : { *(.text.exec) }
 ASSERT(SIZEOF(.exec) <= 0xe00, "handoff overlaps state")
 . = 0x61f01000;
 .state : { *(.state) }
 /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.note*) }
}
