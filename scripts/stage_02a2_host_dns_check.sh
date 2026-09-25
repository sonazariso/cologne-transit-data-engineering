#!/bin/zsh

# Stage 02A.2 host-side DNS diagnostic. This script performs DNS lookups only.

set -u

typeset host_name
typeset python_result
typeset dscache_result
typeset dscache_output
typeset dig_result
typeset dig_output
typeset dig_ips
typeset scutil_output
typeset resolver_blocks
typeset scutil_result
typeset final_result
typeset public_result
typeset public_query_ran
typeset mdd_python_result
typeset mdd_dscache_result
typeset mdd_dig_result
typeset example_python_result
typeset example_dig_result
typeset python_available
typeset dscache_available
typeset dig_available
typeset scutil_available
typeset mdd_python_status
typeset mdd_dig_status
typeset example_python_status
typeset example_dig_status
typeset python_rc
typeset scutil_rc

python_available=YES
dscache_available=YES
dig_available=YES
scutil_available=YES

if ! command -v python3 >/dev/null 2>&1; then
  python_available=NO
fi
if ! command -v dscacheutil >/dev/null 2>&1; then
  dscache_available=NO
fi
if ! command -v dig >/dev/null 2>&1; then
  dig_available=NO
fi
if ! command -v scutil >/dev/null 2>&1; then
  scutil_available=NO
fi

python_host_check() {
  typeset check_host="$1"

  if [[ "$python_available" != YES ]]; then
    python_result="UNAVAILABLE"
    return 0
  fi

  python_result="$(python3 - "$check_host" <<'PY'
import socket
import sys

hostname = sys.argv[1]

try:
    infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
except Exception:
    print("DOES_NOT_RESOLVE")
    raise SystemExit(0)

addresses = sorted({
    item[4][0]
    for item in infos
    if len(item) > 4 and item[4] and item[4][0]
})

if addresses:
    print("RESOLVES " + " ".join(addresses))
else:
    print("DOES_NOT_RESOLVE")
PY
  )"
  python_rc=$?

  if (( python_rc != 0 )) || [[ -z "$python_result" ]]; then
    python_result="UNAVAILABLE"
  fi
}

dscache_host_check() {
  typeset check_host="$1"

  if [[ "$dscache_available" != YES ]]; then
    dscache_result="UNAVAILABLE"
    return 0
  fi

  dscache_output="$(dscacheutil -q host -a name "$check_host" 2>/dev/null)"
  if [[ -n "$dscache_output" ]]; then
    dscache_result="RECORD_RETURNED"
  else
    dscache_result="NO_RECORD"
  fi
}

dig_a_check() {
  typeset check_host="$1"
  typeset resolver_address="$2"

  if [[ "$dig_available" != YES ]]; then
    dig_result="UNAVAILABLE"
    return 0
  fi

  if [[ -n "$resolver_address" ]]; then
    dig_output="$(dig +time=2 +tries=1 @"$resolver_address" A "$check_host" 2>/dev/null)"
  else
    dig_output="$(dig +time=2 +tries=1 A "$check_host" 2>/dev/null)"
  fi

  dig_ips="$(printf '%s\n' "$dig_output" | awk '
    $0 !~ /^[[:space:]]*;/ {
      for (i = 1; i < NF; i++) {
        if ($i == "A") {
          print $(i + 1)
          break
        }
      }
    }
  ' | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')"

  if [[ -n "$dig_ips" ]]; then
    dig_result="RESOLVES $dig_ips"
  else
    dig_result="DOES_NOT_RESOLVE"
  fi
}

printf '%s\n' 'Stage 02A.2 host DNS diagnostic'
printf '%s\n' 'DNS lookups only; no returned address is contacted.'
printf '%s\n' ''

mdd_python_result="UNAVAILABLE"
mdd_dscache_result="UNAVAILABLE"
mdd_dig_result="UNAVAILABLE"
example_python_result="UNAVAILABLE"
example_dig_result="UNAVAILABLE"

for host_name in mdd.gorheinland.com ckan.mdd.gorheinland.com gorheinland.com example.com; do
  python_host_check "$host_name"
  dscache_host_check "$host_name"
  dig_a_check "$host_name" ""

  printf 'Python host resolver: %s -> %s\n' "$host_name" "$python_result"
  printf 'dscacheutil summary: %s -> %s\n' "$host_name" "$dscache_result"
  printf 'Default DNS resolver: %s -> %s\n' "$host_name" "$dig_result"

  case "$host_name" in
    mdd.gorheinland.com)
      mdd_python_result="$python_result"
      mdd_dscache_result="$dscache_result"
      mdd_dig_result="$dig_result"
      ;;
    example.com)
      example_python_result="$python_result"
      example_dig_result="$dig_result"
      ;;
  esac
done

if [[ "$scutil_available" == YES ]]; then
  scutil_output="$(scutil --dns 2>/dev/null)"
  scutil_rc=$?
  if (( scutil_rc != 0 )); then
    scutil_result="UNAVAILABLE"
  else
    resolver_blocks="$(printf '%s\n' "$scutil_output" | awk '
      /^[[:space:]]*resolver #[0-9]+/ { count += 1 }
      END { print count + 0 }
    ')"
    if [[ "$scutil_output" == *"No DNS configuration available"* ]] ||
       [[ "$resolver_blocks" == 0 ]]; then
      scutil_result="DNS configuration present: NO; resolver blocks: 0"
    else
      scutil_result="DNS configuration present: YES; resolver blocks: $resolver_blocks"
    fi
  fi
else
  scutil_result="UNAVAILABLE"
fi

printf '%s\n' ''
printf 'scutil summary: %s\n' "$scutil_result"

case "$mdd_python_result" in
  RESOLVES*) mdd_python_status="RESOLVES" ;;
  DOES_NOT_RESOLVE) mdd_python_status="DOES_NOT_RESOLVE" ;;
  *) mdd_python_status="UNAVAILABLE" ;;
esac

case "$mdd_dig_result" in
  RESOLVES*) mdd_dig_status="RESOLVES" ;;
  DOES_NOT_RESOLVE) mdd_dig_status="DOES_NOT_RESOLVE" ;;
  *) mdd_dig_status="UNAVAILABLE" ;;
esac

case "$example_python_result" in
  RESOLVES*) example_python_status="RESOLVES" ;;
  DOES_NOT_RESOLVE) example_python_status="DOES_NOT_RESOLVE" ;;
  *) example_python_status="UNAVAILABLE" ;;
esac

case "$example_dig_result" in
  RESOLVES*) example_dig_status="RESOLVES" ;;
  DOES_NOT_RESOLVE) example_dig_status="DOES_NOT_RESOLVE" ;;
  *) example_dig_status="UNAVAILABLE" ;;
esac

public_result="NOT_RUN"
public_query_ran=NO

if [[ "$mdd_python_status" == DOES_NOT_RESOLVE ]]; then
  if [[ "$mdd_dig_status" != RESOLVES ]]; then
    if [[ "$dig_available" == YES ]]; then
      public_query_ran=YES
      dig_a_check "mdd.gorheinland.com" "1.1.1.1"
      public_result="$dig_result"
    else
      public_result="UNAVAILABLE"
    fi
  fi
fi

if [[ "$public_query_ran" == YES ]]; then
  printf 'Public resolver control: mdd.gorheinland.com -> %s\n' "$public_result"
else
  printf 'Public resolver control: %s\n' "$public_result"
fi

final_result="INCONCLUSIVE"

if [[ "$python_available" == YES ]] &&
   [[ "$dscache_available" == YES ]] &&
   [[ "$scutil_available" == YES ]]; then
  if [[ "$mdd_python_status" == RESOLVES ]]; then
    final_result="NORMAL_RESOLVER_WORKS"
  elif [[ "$mdd_python_status" == DOES_NOT_RESOLVE ]]; then
    if [[ "$mdd_dig_status" == RESOLVES ]]; then
      final_result="INCONCLUSIVE"
    elif [[ "$public_result" == RESOLVES* ]]; then
      final_result="NORMAL_RESOLVER_FAILS_PUBLIC_DNS_WORKS"
    elif [[ "$example_python_status" == DOES_NOT_RESOLVE ]]; then
      if [[ "$dig_available" == YES ]] &&
         [[ "$example_dig_status" == RESOLVES ]]; then
        final_result="INCONCLUSIVE"
      else
        final_result="GENERAL_HOST_DNS_FAILURE"
      fi
    fi
  fi
fi

printf '%s\n' "HOST_DNS_RESULT: $final_result"
exit 0
