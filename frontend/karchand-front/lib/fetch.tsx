function MakeQueryString(params: { [key: string]: string }) {
  const queryString = params
    ? Object.entries(params)
        .map((param) => {
          return `${param[0]}=${param[1]}`;
        })
        .join("&")
    : "";

  return queryString;
}

export function getFetch(
  url: string,
  params?: { [key: string]: string },
  headers?: { [key: string]: string },
  credentials: "include" | "omit" | "same-origin" = "include"
) {
  const queryString = MakeQueryString(params ?? {});
  const token = localStorage.getItem("authToken")
  console.log("token",token)
  const defaultHeaders = {
    "Content-Type": "application/json",
    "Authorization": token? ("Bearer " + token) : ""
  };

  return fetch(url + (queryString ? "" : "") + queryString, {
    method: "GET",
    headers: {
      ...defaultHeaders,
      ...headers,
    },
  });
}

export function postFetch(
  url: string,
  data: any,
  params?: { [key: string]: string }
) {
  const queryString = MakeQueryString(params ?? {});
  const token = localStorage.getItem("authToken");
  
  // Determine content type and format body
  let finalBody: BodyInit;
  let contentType: string | null = null;

  if (data instanceof FormData) {
    finalBody = data;
    // Don't set Content-Type for FormData - let browser handle it
  } else {
    finalBody = typeof data === 'string' ? data : JSON.stringify(data);
    contentType = 'application/json';
  }

  // Build headers conditionally
  const headers: HeadersInit = {
    "Authorization": token ? ("Bearer " + token) : ""
  };

  // Only add Content-Type header if it's not FormData
  if (contentType) {
    headers["Content-Type"] = contentType;
  }

  return fetch(url + "?" + queryString, {
    method: "POST",
    headers: headers,
    body: finalBody,
  });
}

export function updateFetch(
  url: string,
  data: any,
  method: "PUT" | "PATCH" = "PUT",
  params?: { [key: string]: string }
) {
  const queryString = MakeQueryString(params ?? {});
  const token = localStorage.getItem("authToken")
  const defaultHeaders = {
    "Content-Type": "application/json",
    "Authorization": token? ("Bearer " + token) : ""
  };
  return fetch(url + "?" + queryString, {
    method: method,
    headers: {
      ...defaultHeaders
    },

    body: data,
  });
}

export function deleteFetch(url: string, params?: { [key: string]: string }) {
  const queryString = MakeQueryString(params ?? {});
  const token = localStorage.getItem("authToken")
  const defaultHeaders = {
    "Content-Type": "application/json",
    "Authorization": token? ("Bearer " + token) : ""
  };
  return fetch(url + "?" + queryString, {
    method: "DELETE",
    headers: {
      ...defaultHeaders
    },
  });
}
