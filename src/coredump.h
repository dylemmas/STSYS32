#ifndef COREDUMP_H
#define COREDUMP_H

#include <stdint.h>
#include <stdbool.h>

// ================= COREDUMP FUNCTIONS =================

// Check if a coredump is stored in flash
bool coredumpIsAvailable();

// Get coredump size (0 if none)
uint32_t coredumpGetSize();

// Read coredump data into buffer, returns bytes read
uint32_t coredumpRead(uint8_t* buffer, uint32_t offset, uint32_t maxLen);

// Erase coredump after download
void coredumpErase();

#endif // COREDUMP_H
