#include <iostream>
#include <vector>

using namespace std;

// Generates primes up to 'limit' using the Sieve of Eratosthenes
vector<bool> sieve_of_eratosthenes(int limit) {
    // Initialize all entries as true. 
    // A value in is_prime[i] will finally be false if i is Not a prime.
    vector<bool> is_prime(limit + 1, true);
    
    // 0 and 1 are not prime numbers
    is_prime[0] = false;
    is_prime[1] = false;

    // Core Eratosthenes logic
    for (int p = 2; p * p <= limit; p++) {
        // If is_prime[p] is not changed, then it is a prime
        if (is_prime[p]) {
            // Update all multiples of p starting from p^2
            // (Multiples smaller than p^2 are already marked by smaller primes)
            for (int i = p * p; i <= limit; i += p) {
                is_prime[i] = false;
            }
        }
    }

    return is_prime;
}

int main() {
    // 1. Set the maximum number you expect in your file
    int max_limit = 1000000000;
    
    cout << "Pre-calculating primes up to " << max_limit << "...\n";
    vector<bool> primes = sieve_of_eratosthenes(max_limit);
    cout << "Sieve complete!\n\n";

    // 2. Read your numbers (Simulated here)
    int file_numbers[] = {1, 2, 4, 17, 561, 99991, 18446747}; // Includes a massive 64-bit prime

    // 3. Instant O(1) lookup
    for (int num : file_numbers) {
        cout << "Testing " << num << ": ";
        
        if (num == 1) {
            cout << "NONE\n";
        } else if (num > max_limit) {
            cout << "OUT OF BOUNDS\n";
        } else if (primes[num]) {
            cout << "Prime\n";
        } else {
            cout << "Composite\n";
        }
    }

    return 0;
}