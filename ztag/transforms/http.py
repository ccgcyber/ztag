from ztag.transform import ZGrabTransform, ZMapTransformOutput
from ztag import protocols, errors
from ztag.transform import Transformable
import re

# That's right, I'm parsing HTML with regex
title_regex = re.compile(r'<title>([\s\S]*?)<\/title>', re.IGNORECASE | re.UNICODE)


# Helper function to return a list containing all of the certificates in its arguments with no
# duplicates (using the "raw" field for comparison)
def certs_union(*args):
    temp = {}
    for certs in args:
        for cert in certs:
            temp[cert["raw"]] = cert
    return [cert for cert in temp.values()]


class HTTPTransform(ZGrabTransform):

    name = "http/generic"
    port = None
    protocol = protocols.HTTP
    subprotocol = protocols.HTTP.GET

    def __init__(self, *args, **kwargs):
        super(HTTPTransform, self).__init__(*args, **kwargs)

    @staticmethod
    def set_tls_handshakes(zout, http):
        """
        Populate zout.certificates and the TLS handshake fields in zout.transformed using the given
        HTTP response.
        If the final result has a TLS handshake, include that in the root 'tls' field. If there were
        redirects, and the first one has a TLS handshake, include that in the root 'tls_initial'
        field. If not, the tls_initial is the same as the tls field.
        Certificates are collected from *all* handshakes (not just the first and last).
        :param zout: The ZMapTransformOutput object to populate.
        :param http: The wrapped parsed scan response (with response and redirect_response_chain
                     in the root).
        """
        # Import locally to avoid circular dependency (TODO: move make_tls_obj out of https.py)
        from ztag.transforms import HTTPSTransform

        request_handshake = http['response']['request']['tls_handshake'].resolve()
        if request_handshake is not None:
            tls_out, tls_certificates = HTTPSTransform.make_tls_obj(request_handshake)
            zout.transformed['tls'] = tls_out
            zout.certificates = certs_union(zout.certificates, tls_certificates)

        chain = http['redirect_response_chain'].resolve()

        if not chain:
            # No chain: if the request has a TLS handshake, it is also the tls_initial.
            if 'tls' in zout.transformed:
                zout.transformed['tls_initial'] = zout.transformed['tls']
            return

        for idx, entry in enumerate(chain):
            handshake = Transformable(entry)["request"]["tls_handshake"].resolve()
            if handshake:
                try:
                    tls_out, tls_certificates = HTTPSTransform.make_tls_obj(handshake)
                    zout.certificates = certs_union(zout.certificates, tls_certificates)
                    if idx == 0:
                        # Only the first entry in the chain can be the tls_initial.
                        zout.transformed['tls_initial'] = tls_out
                except (TypeError, KeyError, IndexError, errors.IgnoreObject):
                    # Don't throw away the whole HTTP request just because you can't get its TLS
                    # handshake.
                    pass

        return None

    def _transform_object(self, obj):
        http = Transformable(obj)
        http_response = http['data']['http']['response']
        zout = ZMapTransformOutput()
        out = dict()
        zout.transformed = out
        error_component = http['error_component'].resolve()
        if error_component is not None and error_component == 'connect':
            raise errors.IgnoreObject("connection error")

        self.set_tls_handshakes(zout, http['data']['http'])

        if http_response is not None:
            status_line = http_response['status_line'].resolve()
            status_code = http_response['status_code'].resolve()
            body = http_response['body'].resolve()
            body_sha256 = http_response['body_sha256'].resolve()
            headers = http_response['headers'].resolve()
            if status_line is not None:
                out['status_line'] = status_line
            if status_code is not None:
                out['status_code'] = status_code
            if body is not None:
                out['body'] = body
                m = title_regex.search(body)
                if m:
                    title = m.group(1)
                    if len(title) > 1024:
                        title = title[0:1024]
                    out['title'] = title.strip()
            if headers:
                # FIXME: This modifies the input?
                if "set_cookie" in headers:
                    del headers["set_cookie"]
                if "date" in headers:
                    del headers["date"]
                for k, v, in headers.iteritems():
                    if k == "unknown":
                        for d in v:
                            if len(d["value"]) < 1:
                                continue
                            d["value"] = d["value"][0]
                    elif v:
                        headers[k] = v[0]
                    else:
                        del headers[k]
                out['headers'] = headers
            if body_sha256:
                out['body_sha256'] = body_sha256

        if len(out) == 0:
            raise errors.IgnoreObject("Empty output dict")

        return zout


class HTTPWWWTransform(HTTPTransform):

    name = "http/www"
    port = None
    protocol = protocols.HTTP_WWW
    subprotocol = protocols.HTTP.GET



class OpenProxyTransform(ZGrabTransform):

    name = "http/openproxy"
    port = None
    protocol = protocols.HTTP
    subprotocol = protocols.HTTP.OPEN_PROXY

    def __init__(self, *args, **kwargs):
        super(OpenProxyTransform, self).__init__(*args, **kwargs)

    def _transform_object(self, obj):
        http = Transformable(obj)
        connect_response = http['data']['http']['connect_response']
        get_response = http['data']['http']['response']

        zout = ZMapTransformOutput()
        out = dict()
        error_component = http['error_component'].resolve()
        if error_component is not None and error_component == 'connect':
            raise errors.IgnoreObject("connection error")

        if connect_response:
            status_line = connect_response['status_line'].resolve()
            status_code = connect_response['status_code'].resolve()
            body = connect_response['body'].resolve()
            headers = connect_response['headers'].resolve()

            if status_line or status_code or body or headers is not None:
                out['connect'] = dict()
            if status_line is not None:
                out['connect']['status_line'] = status_line
            if status_code is not None:
                out['connect']['status_code'] = status_code
            if body is not None:
                out['connect']['body'] = body
            if headers is not None:
                out['connect']['headers'] = headers

        if get_response:
            status_line = get_response['status_line'].resolve()
            status_code = get_response['status_code'].resolve()
            body = get_response['body'].resolve()
            headers = get_response['headers'].resolve()
            body_sha256 = get_response['body_sha256'].resolve()
            out['get'] = dict()
            #if body:
            #    random_present = "Uh2Qn8Y7NPRm6h3xqEXUq4EhtW7Po4gy" in body
            #else:
            #    random_present = False
            #out['get']['random_present'] = random_present

            if status_line:
                out['get']['status_line'] = status_line
            if status_code:
                out['get']['status_code'] = status_code
            if body:
                out['get']['body'] = body
            if headers:
                out['get']['headers'] = headers
            if body_sha256:
                out['get']['body_sha256'] = body_sha256

        if len(out) == 0:
            raise errors.IgnoreObject("Empty output dict")

        zout.transformed = out
        return zout
