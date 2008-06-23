import logging, sys
from zipfile import BadZipfile

from django.http import HttpResponseRedirect, Http404, HttpResponse
from django.shortcuts import render_to_response
from django.core.urlresolvers import reverse

from models import EpubArchive, HTMLFile, UserPrefs, StylesheetFile, ImageFile, unsafe_name, get_system_info
from forms import EpubValidateForm
from epub import constants as epub_constants
from epub import InvalidEpubException


def index(request):

    common = _common(request, load_prefs=True)
    user = users.get_current_user()

    documents = EpubArchive.all()
    documents.filter('owner =', user)

    if not user:
        return render_to_response('login.html',  {'common':common,
                                                  'login':users.create_login_url('/')})

    return render_to_response('index.html', {'documents':documents, 
                                             'common':common})

def profile(request):
    common = _check_switch_modes(request)
    return render_to_response('profile.html', { 'common':common})
    

def view(request, title, key):
    logging.info("Looking up title %s, key %s" % (title, key))
    common = _check_switch_modes(request)
    document = _get_document(title, key)

    toc = HTMLFile.gql('WHERE archive = :parent ORDER BY order ASC', 
                   parent=document).fetch(100)
    
    return render_to_response('view.html', {'document':document, 
                                            'toc':toc,
                                            'common':common})

def about(request):
    common = _common(request)
    return render_to_response('about.html', {'common': common})
    

def delete(request):
    '''Delete a book and associated metadata, and decrement our total books counter'''

    if request.POST.has_key('key') and request.POST.has_key('title'):
        title = request.POST['title']
        key = request.POST['key']
        logging.info("Deleting title %s, key %s" % (title, key))
        if users.is_current_user_admin():
            document = _get_document(title, key, override_owner=True)
        else:
            document = _get_document(title, key)
        _delete_document(document)

    return HttpResponseRedirect('/')

def profile_delete(request):
    common = _common(request)

    if not request.POST.has_key('delete'):
        # Extra sanity-check that this is a POST request
        logging.error('Received deletion request but was not POST')
        message = "There was a problem with your request to delete this profile."
        return render_to_response('profile.html', { 'common':common, 'message':message})

    if not request.POST['delete'] == users.get_current_user().nickname():
        # And that we're POSTing from our own form (this is a sanity check, 
        # not a security feature.  The current logged-in user profile is always
        # the one to be deleted, regardless of the value of 'delete')
        logging.error('Received deletion request but nickname did not match: received %s but current user is %s' % (request.POST['delete'], users.get_current_user().nickname()))
        message = "There was a problem with your request to delete this profile."
        return render_to_response('profile.html', { 'common':common, 'message':message})

    userprefs = _prefs()
    userprefs.delete()

    # Decrement our total-users counter
    counter = get_system_info()
    counter.total_users -= 1
    counter.put()
    memcache.set('total_users', counter.total_users)

    # Delete all their books (this is likely to time out for large numbers of books)
    documents = EpubArchive.all()
    common = _common(request, load_prefs=True)
    user = common['user']
    documents.filter('owner =', user)

    for d in documents:
        _delete_document(d)
    
    return HttpResponseRedirect(users.create_logout_url('/'))

def _check_switch_modes(request):
    '''Did they switch viewing modes?'''
    common = _common(request, load_prefs=True)
    userprefs = common['prefs']
    update_cache = False

    if request.GET.has_key('iframe'):
        userprefs.use_iframe = (request.GET['iframe'] == 'yes')
        userprefs.put()
        update_cache = True

    if request.GET.has_key('iframe_note'):
        userprefs.show_iframe_note = (request.GET['iframe_note'] == 'yes')
        userprefs.put()
        update_cache = True

    if update_cache:
        counter = get_system_info()
        memcache.set('total_users', counter.total_users)

    return common
    
def view_chapter(request, title, key, chapter_id):
    logging.info("Looking up title %s, key %s, chapter %s" % (title, key, chapter_id))    
    document = _get_document(title, key)

    chapter = HTMLFile.gql('WHERE archive = :parent AND idref = :idref',
                           parent=document, idref=chapter_id).get()
    stylesheets = StylesheetFile.gql('WHERE archive = :parent',
                                     parent=document).fetch(10)
    next = _chapter_next_previous(document, chapter, 'next')
    previous = _chapter_next_previous(document, chapter, 'previous')

    parent_chapter = None
    subchapter_href = None

    toc = document.get_top_level_toc()

    for t in toc:
        href = chapter.idref.encode(epub_constants.ENC)
        if href in [c.href() for c in t.find_children()]:
            parent_chapter = t
            subchapter_href = href
            logging.info(parent_chapter.order())
            break

    common = _check_switch_modes(request)
        
    return render_to_response('view.html', {'common':common,
                                            'document':document,
                                            'next':next,
                                            'toc':toc,
                                            'subchapter_href':subchapter_href,
                                            'parent_chapter':parent_chapter,
                                            'stylesheets':stylesheets,
                                            'previous':previous,
                                            'chapter':chapter})

def _chapter_next_previous(document, chapter, dir='next'):

    if dir == 'previous':
        argument = '<='
        ordinal = chapter.order - 1
        direction = 'DESC'
    else:
        argument = '>='
        ordinal = chapter.order + 1
        direction = 'ASC'
    
    return HTMLFile.gql('WHERE archive = :parent AND order %s :order ORDER by order %s' 
                        % (argument, direction), 
                        parent=document,
                        order=ordinal).get()


    
def view_chapter_image(request, title, key, image):
    logging.info("Image request: looking up title %s, key %s, image %s" % (title, key, image))        
    document = _get_document(title, key)
    image = ImageFile.gql('WHERE archive = :parent AND idref = :idref',
                          parent=document, idref=image).get()
    if not image:
        raise Http404
    response = HttpResponse(content_type=image.content_type)
    if image.content_type == 'image/svg+xml':
        response.content = image.file
    else:
        response.content = image.data

    return response


def view_chapter_frame(request, title, key, chapter_id):
    '''Generate an iframe to display the document online, possibly with its own stylesheets'''
    document = _get_document(title, key)
    logging.info(request.get_full_path())
    chapter = HTMLFile.gql('WHERE archive = :parent AND idref = :idref',
                           parent=document, idref=chapter_id).get()    
    stylesheets = StylesheetFile.gql('WHERE archive = :parent',
                                     parent=document).fetch(10)
    next = _chapter_next_previous(document, chapter, 'next')
    previous = _chapter_next_previous(document, chapter, 'previous')

    return render_to_response('frame.html', {'document':document, 
                                             'chapter':chapter, 
                                             'next':next,
                                             'previous':previous,
                                             'stylesheets':stylesheets})

def view_stylesheet(request, title, key, stylesheet_id):
    document = _get_document(title, key)
    logging.info('getting stylesheet %s' % stylesheet_id)
    stylesheet = StylesheetFile.gql('WHERE archive = :parent AND idref = :idref',
                                    parent=document,
                                    idref=stylesheet_id).get()
    response = HttpResponse(content=stylesheet.file, content_type='text/css')
    response['Cache-Control'] = 'public'

    return response

def download_epub(request, title, key):
    document = _get_document(title, key)
    response = HttpResponse(content=document.content, content_type=epub_constants.MIMETYPE)
    response['Content-Disposition'] = 'attachment; filename=%s' % document.name
    return response

def upload(request):
    '''Uploads a new document and stores it in the datastore'''
    
    common = _common(request)
    
    document = None 
    
    if request.method == 'POST':
        form = EpubValidateForm(request.POST, request.FILES)
        if form.is_valid():

            data = form.cleaned_data['epub'].content
            document_name = form.cleaned_data['epub'].filename
            logging.info("Document name: %s" % document_name)
            document = EpubArchive(name=document_name)
            document.content = data
            document.owner = users.get_current_user()
            document.put()

            try:
                document.explode()
                document.put()
                sysinfo = get_system_info()
                sysinfo.total_books += 1
                sysinfo.put()
                # Update the cache
                memcache.set('total_books', sysinfo.total_books)

            except BadZipfile:
                logging.error('Non-zip archive uploaded: %s' % document_name)
                message = 'The file you uploaded was not recognized as an ePub archive and could not be added to your library.'
                document.delete()
                return render_to_response('upload.html', {'common':common,
                                                          'form':form, 
                                                          'message':message})
            except InvalidEpubException:
                logging.error('Non epub zip file uploaded: %s' % document_name)
                message = 'The file you uploaded was a valid zip file but did not appear to be an ePub archive.'
                document.delete()
                return render_to_response('upload.html', {'common':common,
                                                          'form':form, 
                                                          'message':message})                
            except:
                # If we got any error, delete this document
                logging.error('Got deadline exceeded error on request, deleting document')
                logging.error(sys.exc_info()[0])
                document.delete()
                raise
            
            logging.info("Successfully added %s" % document.title)
            return HttpResponseRedirect('/')

        return HttpResponseRedirect('/')

    else:
        form = EpubValidateForm()        

    return render_to_response('upload.html', {'common':common,
                                              'form':form, 
                                              'document':document})



def _delete_document(document):
    # Delete the chapters of the book
    toc = HTMLFile.gql('WHERE archive = :parent', 
                   parent=document).fetch(100)
    if toc:
        db.delete(toc)

    # Delete all the stylesheets in the book
    css = StylesheetFile.gql('WHERE archive = :parent', 
                             parent=document).fetch(100)

    if css:
        db.delete(css)

    # Delete all the images in the book
    images = ImageFile.gql('WHERE archive = :parent', 
                             parent=document).fetch(100)

    if images:
        db.delete(images)

    # Delete the book itself, and decrement our counter
    document.delete()
    sysinfo = get_system_info()
    sysinfo.total_books -= 1
    sysinfo.put() 
    memcache.set('total_books', sysinfo.total_books)

def _get_document(title, key, override_owner=False):
    '''Return a document by Google key and owner.  Setting override_owner
    will search regardless of ownership, for use with admin accounts.'''
    user = users.get_current_user()

    document = EpubArchive.get(db.Key(key))
      
    if not document:
        logging.error("Failed to get document with title '%s', key '%s'" 
                      % (unsafe_name(title), key))
        raise Http404 

    if not override_owner and document.owner != user and not users.is_current_user_admin():
        logging.error('User %s tried to access document %s, which they do not own' % (user, title))
        raise Http404

    return document



def _greeting():
    user = users.get_current_user()
    if user:
        text = ('Signed in as %s: <a href="%s">logout</a> | <a href="%s">edit profile</a>' % 
                (user.nickname(), 
                 users.create_logout_url("/"),
                 reverse('library.views.profile')
                 )
                )
        if users.is_current_user_admin():
            text += ' | <a href="%s">admin</a> ' % reverse('library.admin.search')
        return text

    return ("<a name='signin' href=\"%s\">Sign in or register</a>." % users.create_login_url("/"))


def _prefs():
    '''Get (or create) a user preferences object for a given user.
    If created, the total number of users counter will be incremented and
    the memcache updated.'''

    user = users.get_current_user()
    if not user:
        return

    q = UserPrefs.gql("WHERE user = :1", user)
    userprefs = q.get()
    if not userprefs:
        logging.info('Creating a userprefs object for %s' % user.nickname)
        # Create a preference object for this user
        userprefs = UserPrefs(user=user)
        userprefs.put()

        # Increment our total-users counter
        counter = get_system_info()

        counter.total_users += 1
        counter.put()
        memcache.set('total_users', counter.total_users)

    return userprefs

def _common(request, load_prefs=False):
    '''Builds a dictionary of common 'globals' 
    @todo cache some of this, like from sysinfo'''

    common = {}
    user = users.get_current_user()
    common['user']  = user
    common['is_admin'] = users.is_current_user_admin()

    # Don't load user prefs unless we need to
    if load_prefs:
        common['prefs'] = _prefs()

    cached_total_books = memcache.get('total_books')

    if cached_total_books is not None:
        common['total_books'] = cached_total_books
    else:
        sysinfo = get_system_info()
        common['total_books'] = sysinfo.total_books
        memcache.set('total_books', sysinfo.total_books)

    cached_total_users = memcache.get('total_users')

    if cached_total_users is not None:
        common['total_users'] = cached_total_users
    else:
        if not sysinfo:
            sysinfo = get_system_info()            
        common['total_users'] = sysinfo.total_users
        memcache.set('total_users', sysinfo.total_users)

    common['greeting'] = _greeting()

    common['upload_form'] = EpubValidateForm()        
    return common



    